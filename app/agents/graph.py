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
    intent: str
    reply: str
    artifacts: dict  # 结构化中间产物：jd_analysis / match_result / grade ...


# 懒加载单例：面试官持有对话历史，进程内复用。
# 检索器单例统一放在 app.rag.retriever（它构造时拉起 chroma/openai 客户端，
# 必须在带锁的单例里完成，否则并发请求会竞态，详见该文件注释）。
# 这里保持懒加载而不是 import 时实例化：否则"没有 Key 就 import 失败"，测试跑不起来。
_interviewer: InterviewerAgent | None = None
_INTERVIEWER_LOCK = threading.Lock()


def get_interviewer() -> InterviewerAgent:
    global _interviewer
    if _interviewer is None:
        with _INTERVIEWER_LOCK:
            if _interviewer is None:
                _interviewer = InterviewerAgent()
    return _interviewer


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


def route_node(state: AgentState) -> dict:
    return {"intent": route(state["user_input"])}


def jd_analyst_node(state: AgentState) -> dict:
    # 前两步是结构化抽取（json 模式不适合逐 token 展示），改为推送阶段状态
    writer = get_stream_writer()
    _emit(writer, {"status": "正在解析 JD 要求…"})
    analysis = analyze_jd(state["user_input"])

    _emit(writer, {"status": "正在检索简历并逐条匹配…"})
    resume_hits = _search_scoped("简历 个人经历 技能 项目", RESUME_CATEGORY, top_k=5)
    match = match_resume(analysis, "\n".join(h["text"] for h in resume_hits))

    summary = f"岗位【{analysis.position}】匹配度 {match.overall_score}/100。\n" + "\n".join(
        f"- {item.requirement}：{item.score} 分" + (f"（{item.gap_advice}）" if item.gap_advice else "")
        for item in match.items[:6]
    )
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
    agent = get_interviewer()
    hits = agent.retrieve_reference(state["user_input"])
    reply = agent.chat_stream(state["user_input"], emit=lambda e: _emit(get_stream_writer(), e),
                              references=_as_chunks(hits))
    return {"reply": reply, "artifacts": {"sources": _sources(hits)}}


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
