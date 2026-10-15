"""人机协同（interrupt / resume）的图级测试。

用假 LLM + 内存检查点，不碰真实 API。覆盖：
1. 开启人审 -> 图在 jd_review 处中断，且**中断前不输出任何正文**；
2. 人工确认（approve）-> 续跑产出匹配结论，历史里 user/assistant 各一条；
3. 人工修正（edit）-> 按修正后的要求重算匹配度，修正结果进 artifacts。

同时钉住两个易错语义：
- 恢复时只有被中断的节点会重跑，前面的重活（检索、JD 抽取）不会重复执行；
- 检查点让 messages 跨请求累积成完整会话，不需要进程内 dict。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import app.agents.graph as graph_mod  # noqa: E402
from app.schemas import JDAnalysis, MatchItem, MatchResult  # noqa: E402


class _FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query, top_k=5, category=None):
        self.calls += 1
        return [{
            "text": "候选人用 LangGraph 做了多 Agent 编排，RAG 检索 MRR 提升 12%",
            "metadata": {"source": "王东风.pdf", "category": "简历素材"},
            "distance": 0.2,
        }]


@pytest.fixture
def env(monkeypatch):
    """返回 (graph, retriever, match_calls)：全部依赖皆假，唯一真东西是 LangGraph 本身。"""
    retriever = _FakeRetriever()
    monkeypatch.setattr(graph_mod, "get_retriever", lambda: retriever)
    monkeypatch.setattr(graph_mod, "search_available", lambda: False)  # 跳过联网补充
    monkeypatch.setattr(graph_mod, "analyze_jd", lambda jd: JDAnalysis(
        position="Agent 应用开发工程师",
        hard_requirements=["3 年 Python 经验", "熟悉 RAG 检索"],
        soft_requirements=["了解 LangGraph"],
        implicit_preferences=["有大厂经历"],
        keywords=["RAG", "LangGraph"],
    ))

    match_calls: list[list[str]] = []

    def fake_match(analysis, context):
        match_calls.append(list(analysis.hard_requirements))
        return MatchResult(overall_score=78, items=[
            MatchItem(requirement="熟悉 RAG 检索", evidence="RAG 检索 MRR 提升 12%", score=80),
            MatchItem(requirement="3 年 Python 经验", evidence="", score=60,
                      gap_advice="补充工作年限的明确说明"),
        ])

    monkeypatch.setattr(graph_mod, "match_resume", fake_match)
    return graph_mod.build_graph(InMemorySaver()), retriever, match_calls


def _stream(graph, payload, thread="t1"):
    deltas: list[str] = []
    statuses: list[str] = []
    final: dict = {}
    interrupt = None
    for mode, chunk in graph.stream(
        payload, config={"configurable": {"thread_id": thread}}, stream_mode=["custom", "values"]
    ):
        if mode == "custom":
            if "delta" in chunk:
                deltas.append(chunk["delta"])
            elif "status" in chunk:
                statuses.append(chunk["status"])
        else:
            final = chunk
            if chunk.get("__interrupt__"):
                interrupt = chunk["__interrupt__"]
    return "".join(deltas), statuses, final, interrupt


def _start(graph, thread="t1"):
    return _stream(graph, {
        "messages": [{"role": "user", "content": "分析这个 JD：Agent 应用开发"}],
        "user_input": "分析这个 JD：Agent 应用开发",
        "session_id": thread,
        "force_intent": "jd_analysis",
        "intent": "",
        "reply": "",
        "artifacts": {},
    }, thread)


def test_interrupt_pauses_before_emitting_text(env):
    graph, _, _ = env
    text, statuses, _final, interrupt = _start(graph)

    assert interrupt is not None, "开启人审后应在 jd_review 处中断"
    payload = interrupt[0].value
    assert payload["type"] == "jd_review"
    assert payload["position"] == "Agent 应用开发工程师"
    assert payload["hard_requirements"] == ["3 年 Python 经验", "熟悉 RAG 检索"]
    assert payload["match_result"]["overall_score"] == 78
    assert "等待确认岗位要求…" in statuses
    # 关键：中断发生在出结论之前，人工没确认就不该有正文
    assert text == ""


def test_approve_resumes_and_records_history(env):
    graph, retriever, _ = env
    _start(graph)

    text, statuses, final, interrupt = _stream(
        graph, Command(resume={"action": "approve"}), "t1"
    )

    assert interrupt is None
    assert "综合匹配度 78/100" in text
    assert "依据简历：「RAG 检索 MRR 提升 12%」" in text
    assert final["artifacts"]["edited_by_human"] is False
    assert final["artifacts"]["reviewed"] is True
    # 检查点让历史跨请求累积：一条用户消息 + 一条助手回复
    assert [m["role"] for m in final["messages"]] == ["user", "assistant"]
    assert final["messages"][0]["content"].startswith("分析这个 JD")
    # 恢复只重跑被中断的节点：检索没有在恢复时再执行一遍
    assert retriever.calls == 1


def test_edit_recomputes_match_with_human_requirements(env):
    graph, _, match_calls = env
    _start(graph)

    text, _statuses, final, interrupt = _stream(graph, Command(resume={
        "action": "edit",
        "hard_requirements": ["5 年 Python 经验", "精通 RAG 与向量检索", "  "],
        "soft_requirements": ["有 LangGraph 实战"],
    }), "t1")

    assert interrupt is None
    # 空白项被丢掉；匹配度按人工修正后的要求重算（第二次调用 fake_match）
    assert final["artifacts"]["jd_analysis"]["hard_requirements"] == [
        "5 年 Python 经验", "精通 RAG 与向量检索",
    ]
    assert final["artifacts"]["edited_by_human"] is True
    assert match_calls[-1] == ["5 年 Python 经验", "精通 RAG 与向量检索"]
    assert "已按人工修正重算匹配度…" in _statuses
    assert "综合匹配度 78/100" in text


def test_history_is_isolated_between_threads(env):
    """不同 thread_id 的历史互不可见——thread_id 就是会话边界。"""
    graph, _, _ = env
    _start(graph, thread="t1")
    _stream(graph, Command(resume={"action": "approve"}), "t1")
    _start(graph, thread="t2")

    t1 = graph.get_state({"configurable": {"thread_id": "t1"}}).values
    t2 = graph.get_state({"configurable": {"thread_id": "t2"}}).values
    assert len(t1["messages"]) == 2
    # t2 刚发起、还没确认，只有一条 user 消息且处于中断态
    assert len(t2["messages"]) == 1


def test_review_can_be_disabled(monkeypatch, env):
    """human_review_jd=False 时退化为全自动：一轮跑完不停顿。"""
    monkeypatch.setattr(
        graph_mod.get_settings(), "human_review_jd", False, raising=False
    )
    graph, _, _ = env
    text, _statuses, final, interrupt = _start(graph, thread="t3")
    assert interrupt is None
    assert "综合匹配度 78/100" in text
    assert len(final["messages"]) == 2
