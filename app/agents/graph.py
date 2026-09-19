"""LangGraph 编排：Supervisor 模式 + 真流式。

状态图：
  START -> route（Planner 意图识别）-> jd_analyst / resume_advisor / interviewer / general_chat -> END

流式方案（面试考点）：
- 节点内部调用 LLM 的 stream=True，边生成边通过 langgraph 的 custom stream writer
  推送增量（{"delta": ...}）或阶段状态（{"status": ...}）；
- API 层以 stream_mode=["custom", "values"] 消费：custom 事件实时转发给前端，
  values 取最终 state 落历史。
"""
import operator
import threading
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from app.agents import llm
from app.agents.interviewer import InterviewerAgent
from app.agents.jd_analyst import analyze_jd, match_resume
from app.agents.planner import route
from app.agents.resume_advisor import advise_stream
from app.rag.retriever import get_retriever
from app.schemas import ChatMessage, RetrievedChunk


class AgentState(TypedDict):
    messages: Annotated[list[ChatMessage], operator.add]  # 对话历史（追加式）
    user_input: str
    session_id: str
    force_intent: str  # 前端显式选择的模式；"auto" 表示由 Planner 路由
    intent: str
    reply: str
    artifacts: dict  # 结构化中间产物：jd_analysis / match_result / grade ...


# 懒加载单例：面试官持有对话历史与挂起题目，必须按会话隔离——
# 全局共享单例会把上个会话的挂起题目泄漏到下个会话（真实踩坑：
# 新会话说"分析这个 JD"却被旧题目的批改流程接管）。
_INTERVIEWERS: dict[str, InterviewerAgent] = {}
_INTERVIEWERS_LOCK = threading.Lock()
_MAX_INTERVIEW_SESSIONS = 50  # 简单防膨胀：超过就淘汰最早创建的会话


def get_interviewer(session_id: str = "default") -> InterviewerAgent:
    with _INTERVIEWERS_LOCK:
        agent = _INTERVIEWERS.get(session_id)
        if agent is None:
            if len(_INTERVIEWERS) >= _MAX_INTERVIEW_SESSIONS:
                _INTERVIEWERS.pop(next(iter(_INTERVIEWERS)))
            agent = _INTERVIEWERS[session_id] = InterviewerAgent()
        return agent


def _emit(writer: Any, event: dict) -> None:
    if writer is not None:
        writer(event)


RESUME_CATEGORY = "简历素材"


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


def _stream_llm(messages: list[dict[str, str]]) -> str:
    """流式调 LLM：每个增量通过 custom stream 推给 API 层，返回完整文本。"""
    writer = get_stream_writer()
    parts: list[str] = []
    for delta in llm.chat_stream(messages):
        parts.append(delta)
        _emit(writer, {"delta": delta})
    return "".join(parts)


# 模拟面试进行中时，候选人回答里不会有"面试"等关键词，逐条消息独立路由
# 会把回答错送进通用聊天（真实踩坑）。所以挂起题目存在时本会话消息默认进面试官，
# 但三类例外要放行：明确结束、重新开一场、明确转向其他任务（如新的 JD 分析）。
INTERVIEW_EXIT_KEYWORDS = ("结束面试", "停止面试", "不面了", "面试结束")
INTERVIEW_START_KEYWORDS = ("模拟面试", "来个面试", "面试我", "开始面试", "考我")
# 强意图词：命中说明用户在面试途中明确发起了其他任务，放行并结束当前面试
STRONG_TASK_KEYWORDS = ("jd", "岗位", "职位", "简历")


def route_node(state: AgentState) -> dict:
    text = state["user_input"]
    agent = get_interviewer(state["session_id"])

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
    # 前两步是结构化抽取（json 模式不适合逐 token 展示），改为推送阶段状态
    writer = get_stream_writer()
    _emit(writer, {"status": "正在解析 JD 要求…"})
    analysis = analyze_jd(state["user_input"])

    _emit(writer, {"status": "正在检索简历并逐条匹配…"})
    resume_hits = _search_scoped("简历 个人经历 技能 项目", RESUME_CATEGORY, top_k=5)
    match = match_resume(analysis, "\n".join(h["text"] for h in resume_hits))

    # 摘要带简历原文引用：每条匹配项标注证据出处，没有证据的给补强建议
    lines = [f"岗位【{analysis.position}】综合匹配度 {match.overall_score}/100", ""]
    for item in match.items[:6]:
        lines.append(f"- {item.requirement}：{item.score} 分")
        if item.evidence:
            lines.append(f"  依据简历：「{item.evidence[:80]}」")
        if item.gap_advice:
            lines.append(f"  建议：{item.gap_advice}")
    summary = "\n".join(lines)
    # 最终摘要分块推送，前端有渐进渲染效果
    for i in range(0, len(summary), 64):
        _emit(writer, {"delta": summary[i:i + 64]})
    return {
        "reply": summary,
        "artifacts": {
            "jd_analysis": analysis.model_dump(),
            "match_result": match.model_dump(),
            "sources": _sources(resume_hits),
        },
    }


def resume_advisor_node(state: AgentState) -> dict:
    hits = _search_scoped(state["user_input"], RESUME_CATEGORY, top_k=5)
    reply = advise_stream(state["user_input"], _as_chunks(hits),
                          emit=lambda e: _emit(get_stream_writer(), e))
    return {"reply": reply, "artifacts": {"sources": _sources(hits)}}


def interviewer_node(state: AgentState) -> dict:
    agent = get_interviewer(state["session_id"])
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
    return {"reply": _stream_llm([{"role": "user", "content": state["user_input"]}])}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("jd_analyst", jd_analyst_node)
    graph.add_node("resume_advisor", resume_advisor_node)
    graph.add_node("interviewer", interviewer_node)
    graph.add_node("general_chat", general_chat_node)

    graph.set_entry_point("route")
    graph.add_conditional_edges("route", lambda s: s["intent"], {
        "jd_analysis": "jd_analyst",
        "resume_advice": "resume_advisor",
        "mock_interview": "interviewer",
        "general_chat": "general_chat",
    })
    for node in ("jd_analyst", "resume_advisor", "interviewer", "general_chat"):
        graph.add_edge(node, END)
    return graph.compile()


app_graph = build_graph()
