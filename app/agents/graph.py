"""LangGraph 编排：Supervisor 模式 + 真流式 + 人工审核（interrupt）。

状态图：
  START -> route（Planner 意图识别）
             ├─ jd_analyst ─> jd_review（人审 interrupt）─┐
             ├─ resume_advisor ───────────────────────────┼─> finalize -> END
             ├─ interviewer ──────────────────────────────┤
             └─ general_chat ─────────────────────────────┘

流式方案（面试考点）：
- 节点内部调用 LLM 的 stream=True，边生成边通过 langgraph 的 custom stream writer
  推送增量（{"delta": ...}）或阶段状态（{"status": ...}）；
- API 层以 stream_mode=["custom", "values"] 消费：custom 事件实时转发给前端，
  values 取最终 state 落历史。

为什么要有 finalize 节点："把回复写进对话历史"是横切关注点，所有分支汇到它再进 END，
就只有一个实现处，将来加 Agent 分支不会漏掉落历史这一步。

Function Calling 的接入范围（面试考点）：
- 只把联网搜索挂给 jd_review 与 general_chat；
- resume_advisor 与 interviewer 刻意不挂——它们的价值恰恰在于"结论必须挂在知识库
  原文引用上"，引入联网内容会破坏这条可追溯闭环。

人工审核（interrupt）的三件套（面试考点）：
1. 图必须带 checkpointer 编译——interrupt 本质是"存下状态 + 抛出中断"；
2. 调用时必须传 thread_id（这里映射 session_id）——中断归属于某条线程；
3. 恢复用 `Command(resume=<人工输入>)` 重新驱动同一条线程。
还有一个极易踩的语义：节点内调用 interrupt() 后，**恢复时整个节点函数会从头再执行**，
interrupt() 这次直接返回人工输入。所以 LLM 调用与流式输出必须放在 interrupt() 之后，
否则每次恢复都会把前面的重活再跑一遍（重复计费、重复副作用）。
"""
import logging
import operator
import threading
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agents.checkpoint import get_checkpointer
from app.agents.interviewer import InterviewerAgent
from app.agents.jd_analyst import analyze_jd, match_resume
from app.agents.planner import route
from app.agents.resume_advisor import advise_stream
from app.config import get_settings
from app.rag.retriever import get_retriever
from app.schemas import JDAnalysis, RetrievedChunk
from app.tools.tool_loop import GENERAL_SYSTEM, run_with_tools
from app.tools.web_search import search_available

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    # 对话历史：由 checkpointer 持久化，用 operator.add 累加（跨请求累积成完整会话）。
    # 存 dict 而不是 ChatMessage 模型：检查点序列化器对 dict 最稳。
    messages: Annotated[list[dict], operator.add]
    user_input: str
    session_id: str
    force_intent: str  # 前端显式选择的模式；"auto" 表示由 Planner 路由
    intent: str
    reply: str
    artifacts: dict  # 结构化中间产物：jd_analysis / match_result / sources / grade ...


# 懒加载缓存：面试官持有对话历史与挂起题目，必须按会话隔离——
# 全局共享单例会把上个会话的挂起题目泄漏到下个会话（真实踩坑：
# 新会话说"分析这个 JD"却被旧题目的批改流程接管）。
# 注意这只是**缓存**：评分记录与面试官上下文已落库（app/storage.py），
# 被淘汰的会话下次访问会从库里重新 load，淘汰不再等于丢数据。
_INTERVIEWERS: dict[str, InterviewerAgent] = {}
_INTERVIEWERS_LOCK = threading.Lock()
_MAX_INTERVIEW_SESSIONS = 50  # 缓存容量上限（防内存膨胀），不是数据保留上限


def get_interviewer(session_id: str = "default") -> InterviewerAgent:
    with _INTERVIEWERS_LOCK:
        agent = _INTERVIEWERS.get(session_id)
        if agent is None:
            if len(_INTERVIEWERS) >= _MAX_INTERVIEW_SESSIONS:
                evicted, _ = _INTERVIEWERS.popitem(last=False)
                logger.warning("面试官缓存淘汰 session=%s（数据已在库中，可重新加载）", evicted)
            agent = _INTERVIEWERS[session_id] = InterviewerAgent(session_id=session_id)
        return agent


def drop_interviewer(session_id: str) -> None:
    """删除会话时同步清掉内存缓存（库里的记录由 storage 层负责删）。"""
    with _INTERVIEWERS_LOCK:
        _INTERVIEWERS.pop(session_id, None)


def _emit(writer: Any, event: dict) -> None:
    if writer is not None:
        writer(event)


RESUME_CATEGORY = "简历素材"
WEB_CATEGORY = "联网搜索"
RESUME_QUERY = "简历 个人经历 技能 项目"

# JD 分析跑完后，交给带工具的对话轮判断能否联网补充岗位/公司背景。
# 刻意做成"选做"：模型认为没有增量价值时直接回答，整段被静默丢弃
# （见 run_with_tools 的 only_if_used），不影响上面的结构化输出。
JD_ENRICH_TITLE = "\n\n---\n\n**岗位背景补充（联网搜索）**\n\n"
JD_ENRICH_SYSTEM = """你在为求职者补充岗位与公司的公开背景信息。
如果联网能查到有助于理解这个岗位的公开信息（公司主营业务、业务现状、岗位市场行情），
就调用 web_search 查询后简要说明；如果没有增量价值，直接回复「无需补充」，
不要为了用工具而搜索。只依据搜索结果陈述，不要编造。"""
JD_ENRICH_PROMPT = """岗位名称：{position}

用户提供的 JD 原文（节选）：
{jd}

请判断是否需要联网补充该岗位或其公司的公开背景信息。"""


def _search_scoped(query: str, category: str, top_k: int = 5) -> list[dict]:
    """按类别检索；该类别为空时降级为全库检索（简历还没入库也能给出回答）。

    面试考点：为什么按 category 过滤？→ 简历问答召回八股文是纯噪声，
    精确率比召回率更影响体验；但空库时必须有兜底，否则功能直接不可用。
    """
    hits = get_retriever().search(query, top_k=top_k, category=category)
    if not hits:
        hits = get_retriever().search(query, top_k=top_k)
    return hits


def _as_chunks(hits: list[dict]) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            text=h["text"],
            source=h["metadata"].get("source", ""),
            category=h["metadata"].get("category", ""),
            score=round(1 - h.get("distance", 0.0), 4),
        )
        for h in hits
    ]


def _sources(hits: list[dict]) -> list[dict]:
    """回传前端的可追溯引用（原文片段 + 来源 + 距离）。"""
    return [
        {
            "text": h["text"][:300],
            "source": h["metadata"].get("source", ""),
            "category": h["metadata"].get("category", ""),
            "distance": round(h.get("distance", 0.0), 4),
        }
        for h in hits
    ]


def _web_sources(results: list[dict]) -> list[dict]:
    """把联网命中映射成与知识库引用一致的 SourceRef 结构，前端可统一渲染。

    distance 置 0：联网结果没有向量距离，前端据此不显示"距离"一项。
    """
    return [
        {
            "text": (r.get("content") or "")[:300],
            "source": r.get("url", ""),
            "category": WEB_CATEGORY,
            "distance": 0.0,
        }
        for r in results
        if r.get("url")
    ]


def _render_jd_summary(analysis: dict, match: dict) -> str:
    """把结构化匹配结果渲染成人类可读的摘要（带简历原文引用）。"""
    lines = [f"岗位【{analysis.get('position', '')}】综合匹配度 {match.get('overall_score', 0)}/100", ""]
    for item in (match.get("items") or [])[:6]:
        lines.append(f"- {item.get('requirement', '')}：{item.get('score', 0)} 分")
        evidence = item.get("evidence") or ""
        if evidence:
            lines.append(f"  依据简历：「{evidence[:80]}」")
        advice = item.get("gap_advice") or ""
        if advice:
            lines.append(f"  建议：{advice}")
    return "\n".join(lines)


# 模拟面试进行中时，候选人回答里不会有"面试"等关键词，逐条消息独立路由
# 会把回答错送进通用聊天（真实踩坑）。所以挂起题目存在时本会话消息默认进面试官，
# 但三类例外要放行：明确结束、重新开一场、明确转向其他任务（如新的 JD 分析）。
INTERVIEW_EXIT_KEYWORDS = ("结束面试", "停止面试", "不面了", "面试结束")
INTERVIEW_START_KEYWORDS = ("模拟面试", "来个面试", "面试我", "开始面试", "考我")
# 强意图词：命中说明用户在面试途中明确发起了其他任务，放行并结束当前面试
STRONG_TASK_KEYWORDS = ("jd", "岗位", "职位", "简历")


def route_node(state: AgentState) -> dict:
    text = state["user_input"]
    agent = get_interviewer(state.get("session_id") or "default")

    # 1) 前端显式选择了模式：直接生效（选择非面试模式时顺带结束当前面试）
    forced = state.get("force_intent") or "auto"
    if forced != "auto":
        if forced != "mock_interview":
            agent.end_interview()
        return {"intent": forced}

    # 2) 没有挂起题目：正常按关键词路由
    if agent.pending_question is None:
        return {"intent": route(text)}

    # 3) 面试进行中：默认本条消息是候选人回答
    low = text.lower()
    if any(kw in text for kw in INTERVIEW_EXIT_KEYWORDS):
        agent.end_interview()
        return {"intent": "general_chat"}  # "结束面试"本身含"面试"，不能还给关键词路由
    if any(kw in text for kw in INTERVIEW_START_KEYWORDS):
        agent.end_interview()  # 重新开一场：清掉旧题，换主题出题
        return {"intent": "mock_interview"}
    if any(kw in low for kw in STRONG_TASK_KEYWORDS):
        agent.end_interview()  # 面试途中明确发起其他任务（如"分析这个 JD：…"）
        return {"intent": route(text)}
    return {"intent": "mock_interview"}


def jd_analyst_node(state: AgentState) -> dict:
    """只产出结构化结果，不出最终文案。

    为什么不在这一步流式出摘要：下一步是人工审核，要求可能被人工修正；
    修正后匹配度要重算、摘要要重出。把"出文案"放到审核之后，就只有一个出口，
    不会出现"先输出一版、修正后再输出一版"的重复内容。
    """
    writer = get_stream_writer()
    _emit(writer, {"status": "正在解析 JD 要求…"})
    analysis = analyze_jd(state["user_input"])

    _emit(writer, {"status": "正在检索简历并逐条匹配…"})
    resume_hits = _search_scoped(RESUME_QUERY, RESUME_CATEGORY, top_k=5)
    match = match_resume(analysis, "\n".join(h["text"] for h in resume_hits))

    return {
        "artifacts": {
            "jd_analysis": analysis.model_dump(),
            "match_result": match.model_dump(),
            "sources": _sources(resume_hits),
        },
    }


def jd_review_node(state: AgentState) -> dict:
    """人工审核节点（interrupt）：确认或修正抽取出的岗位要求，然后出结论。

    注意本节点会在恢复时被整体重跑（见模块 docstring），
    所以 interrupt() 之后的代码才是真正"只执行一次"的部分。
    """
    writer = get_stream_writer()
    artifacts = dict(state.get("artifacts") or {})
    analysis = dict(artifacts.get("jd_analysis") or {})
    match = dict(artifacts.get("match_result") or {})
    resume_hits: list[dict] = []

    if get_settings().human_review_jd:
        _emit(writer, {"status": "等待确认岗位要求…"})
        decision = interrupt({
            "type": "jd_review",
            "position": analysis.get("position", ""),
            "hard_requirements": analysis.get("hard_requirements") or [],
            "soft_requirements": analysis.get("soft_requirements") or [],
            "match_result": match,
            "hint": "确认抽取出的岗位要求是否需要修正；确认后继续生成匹配结论。",
        })
    else:
        decision = {"action": "approve"}

    decision = decision if isinstance(decision, dict) else {"action": "approve"}
    edited = False
    if decision.get("action") == "edit":
        analysis["hard_requirements"] = _clean_list(decision.get("hard_requirements"))
        analysis["soft_requirements"] = _clean_list(decision.get("soft_requirements"))
        edited = True
        _emit(writer, {"status": "已按人工修正重算匹配度…"})
        resume_hits = _search_scoped(RESUME_QUERY, RESUME_CATEGORY, top_k=5)
        match = match_resume(
            JDAnalysis(**analysis), "\n".join(h["text"] for h in resume_hits)
        ).model_dump()

    summary = _render_jd_summary(analysis, match)
    _emit(writer, {"status": "正在生成匹配结论…"})
    # 最终摘要分块推送，前端有渐进渲染效果
    for i in range(0, len(summary), 64):
        _emit(writer, {"delta": summary[i:i + 64]})

    # 结构化流水线跑完后，让 LLM 自主决定要不要联网补充岗位/公司背景。
    # 放在最后：联网内容是"补充"，不能打乱上面带简历原文引用的主体结论。
    # 没配搜索 Key 时整段跳过，避免多一次无意义的 LLM 调用。
    web_sources: list[dict] = []
    if search_available():
        _emit(writer, {"status": "正在核对是否需要补充岗位背景…"})
        run = run_with_tools(
            [{
                "role": "user",
                "content": JD_ENRICH_PROMPT.format(
                    position=analysis.get("position", ""), jd=state["user_input"][:800]
                ),
            }],
            system=JD_ENRICH_SYSTEM,
            writer=writer,
            prefix=JD_ENRICH_TITLE,
            only_if_used=True,
        )
        summary += run.text
        web_sources = _web_sources(run.web_results)

    base_sources = _sources(resume_hits) if edited else (artifacts.get("sources") or [])
    return {
        "reply": summary,
        "artifacts": {
            "jd_analysis": analysis,
            "match_result": match,
            "sources": base_sources + web_sources,
            "reviewed": bool(get_settings().human_review_jd),
            "edited_by_human": edited,
        },
    }


def _clean_list(value: Any) -> list[str]:
    """人工提交的字符串列表：去空白、丢空项（前端 textarea 按行拆分时很常见）。"""
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def resume_advisor_node(state: AgentState) -> dict:
    hits = _search_scoped(state["user_input"], RESUME_CATEGORY, top_k=5)
    reply = advise_stream(state["user_input"], _as_chunks(hits),
                          emit=lambda e: _emit(get_stream_writer(), e))
    return {"reply": reply, "artifacts": {"sources": _sources(hits)}}


def interviewer_node(state: AgentState) -> dict:
    agent = get_interviewer(state.get("session_id") or "default")
    writer = get_stream_writer()
    hits = agent.retrieve_reference(state["user_input"])
    # 面试官是状态机：无挂起问题则出题，有挂起问题则批改+追问（见 interviewer.py）
    reply, extra = agent.next_turn(
        state["user_input"],
        emit=lambda e: _emit(writer, e),
        references=_as_chunks(hits),
    )
    return {"reply": reply, "artifacts": {"sources": _sources(hits), **extra}}


def general_chat_node(state: AgentState) -> dict:
    # 通用对话是唯一的"自由对话"入口：挂上搜索工具，由 LLM 自行判断该不该联网。
    # 没配搜索 Key 时 run_with_tools 自动退化成普通流式对话。
    run = run_with_tools(
        [{"role": "user", "content": state["user_input"]}],
        system=GENERAL_SYSTEM,
        writer=get_stream_writer(),
    )
    artifacts = {"sources": _web_sources(run.web_results)} if run.used_tools else {}
    return {"reply": run.text, "artifacts": artifacts}


def finalize_node(state: AgentState) -> dict:
    """统一收口：把本轮回复追加进对话历史（messages 由 checkpointer 持久化）。

    放在所有 Agent 分支汇合处，保证"落历史"只有一处实现。
    """
    return {"messages": [{"role": "assistant", "content": state.get("reply") or ""}]}


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """构建并编译状态图。

    checkpointer 可注入：生产用 SQLite 单例（跨进程/重启可续），
    单测注入 InMemorySaver 以保证用例之间互不污染。
    """
    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("jd_analyst", jd_analyst_node)
    graph.add_node("jd_review", jd_review_node)
    graph.add_node("resume_advisor", resume_advisor_node)
    graph.add_node("interviewer", interviewer_node)
    graph.add_node("general_chat", general_chat_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("route")
    graph.add_conditional_edges("route", lambda s: s["intent"], {
        "jd_analysis": "jd_analyst",
        "resume_advice": "resume_advisor",
        "mock_interview": "interviewer",
        "general_chat": "general_chat",
    })
    # JD 分析多一步人工审核，其余分支直接收口
    graph.add_edge("jd_analyst", "jd_review")
    for node in ("jd_review", "resume_advisor", "interviewer", "general_chat"):
        graph.add_edge(node, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or get_checkpointer())


app_graph = build_graph()
