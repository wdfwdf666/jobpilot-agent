"""LangGraph 编排：Supervisor 模式骨架。

状态图：
  START -> route（Planner 意图识别）-> jd_analyst / resume_advisor / interviewer / general_chat -> END

TODO(P1):
- 子 Agent 之间需要交接时（如 JD 分析 -> 简历优化），由 supervisor 二次路由；
- 接入联网搜索工具节点（Function Calling）。
"""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.interviewer import InterviewerAgent
from app.agents.jd_analyst import analyze_jd, match_resume
from app.agents.planner import route
from app.agents.resume_advisor import advise
from app.rag.retriever import Retriever
from app.schemas import ChatMessage, RetrievedChunk


class AgentState(TypedDict):
    messages: Annotated[list[ChatMessage], operator.add]  # 对话历史（追加式）
    user_input: str
    intent: str
    reply: str
    artifacts: dict  # 结构化中间产物：jd_analysis / match_result / grade ...


# 懒加载单例：检索器/面试官持有 API 客户端，不能在 import 时实例化，
# 否则"没有 Key 就 import 失败"，测试和 CI 都跑不起来。
_retriever: Retriever | None = None
_interviewer: InterviewerAgent | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


def get_interviewer() -> InterviewerAgent:
    global _interviewer
    if _interviewer is None:
        _interviewer = InterviewerAgent()
    return _interviewer


def route_node(state: AgentState) -> dict:
    return {"intent": route(state["user_input"])}


def jd_analyst_node(state: AgentState) -> dict:
    analysis = analyze_jd(state["user_input"])
    resume_chunks = get_retriever().search("简历 个人经历 技能 项目", top_k=5)
    match = match_resume(analysis, "\n".join(c.text for c in resume_chunks))
    summary = f"岗位【{analysis.position}】匹配度 {match.overall_score}/100。\n" + "\n".join(
        f"- {item.requirement}：{item.score} 分" + (f"（{item.gap_advice}）" if item.gap_advice else "")
        for item in match.items[:6]
    )
    return {"reply": summary, "artifacts": {"jd_analysis": analysis.model_dump(), "match_result": match.model_dump()}}


def resume_advisor_node(state: AgentState) -> dict:
    chunks = get_retriever().search(state["user_input"], top_k=5)
    retrieved = [RetrievedChunk(text=c["text"], source=c["metadata"].get("source", ""),
                                category=c["metadata"].get("category", "")) for c in chunks]
    return {"reply": advise(state["user_input"], retrieved)}


def interviewer_node(state: AgentState) -> dict:
    reply = get_interviewer().chat(state["user_input"])
    return {"reply": reply}


def general_chat_node(state: AgentState) -> dict:
    from app.agents.llm import chat
    return {"reply": chat([{"role": "user", "content": state["user_input"]}])}


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
