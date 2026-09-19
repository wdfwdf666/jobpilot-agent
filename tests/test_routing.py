"""路由状态机回归测试：面试挂起期间的消息分流。

背景（真实事故）：面试官有挂起题目时，"分析这个 JD：…"也被劫持进面试官；
另一个会话的"模拟面试，考我 rag"却被拿去批改上一场的旧题。
"""
import pytest

from app.agents.graph import get_interviewer, route_node


def _state(text: str, sid: str, force: str = "auto") -> dict:
    return {"user_input": text, "session_id": sid, "force_intent": force}


def test_pending_interview_captures_answers():
    agent = get_interviewer("rt-a")
    agent._pending_question = "什么是 GIL？"
    try:
        out = route_node(_state("GIL 是全局解释器锁，同一时刻只有一个线程执行字节码", "rt-a"))
        assert out["intent"] == "mock_interview"
    finally:
        agent.end_interview()


def test_jd_request_during_interview_escapes():
    agent = get_interviewer("rt-b")
    agent._pending_question = "什么是 GIL？"
    try:
        out = route_node(_state("分析这个 JD：负责 Agent 应用研发，要求熟悉 RAG", "rt-b"))
        assert out["intent"] == "jd_analysis"
        # 明确转向其他任务时面试已结束，不会残留挂起题目
        assert agent.pending_question is None
    finally:
        agent.end_interview()


def test_restart_switches_topic():
    agent = get_interviewer("rt-c")
    agent._pending_question = "旧的 FastAPI 题"
    out = route_node(_state("模拟面试，考我 RAG", "rt-c"))
    assert out["intent"] == "mock_interview"
    assert agent.pending_question is None  # 旧题已清空，下一轮按新主题出题
    agent.end_interview()


def test_exit_keyword_goes_general_chat():
    agent = get_interviewer("rt-d")
    agent._pending_question = "什么是 GIL？"
    out = route_node(_state("结束面试", "rt-d"))
    assert out["intent"] == "general_chat"
    assert agent.pending_question is None


def test_forced_mode_skips_routing_and_ends_interview():
    agent = get_interviewer("rt-e")
    agent._pending_question = "什么是 GIL？"
    out = route_node(_state("随便说点什么", "rt-e", force="jd_analysis"))
    assert out["intent"] == "jd_analysis"
    assert agent.pending_question is None


def test_sessions_are_isolated():
    a1 = get_interviewer("rt-iso-1")
    a2 = get_interviewer("rt-iso-2")
    assert a1 is not a2
    a1._pending_question = "会话 1 的题"
    assert a2.pending_question is None  # 会话 1 的挂起题目不能泄漏到会话 2
    a1.end_interview()
