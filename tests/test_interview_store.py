"""面试记录落盘的存储层测试。

为什么测存储层而不是测 InterviewerAgent 全流程：
- Agent.__init__ 会初始化真实 Retriever / 向量库，单测不该背这个成本
  （test_interview_report.py 用 object.__new__ 旁路也是同一个原因）；
- "重启不丢"的本质在存储层：写进去、换一个新连接还能读回来，就等价于重启恢复。

用临时文件库（不是内存库）：验证的恰恰是"落盘"，内存库证明不了这件事。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage  # noqa: E402


def _fresh_store() -> None:
    """每个用例一个全新临时库：互不污染，也顺带覆盖"从空库建表"的路径。"""
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    Path(f.name).unlink()  # 只要路径，空文件交给 executescript 建表
    storage.init(sqlite3.connect(f.name, check_same_thread=False))


def test_records_roundtrip_across_connections():
    _fresh_store()
    rec = {
        "question": "什么是 RRF？",
        "answer": "倒数排名融合",
        "score": 60,
        "correct_points": ["说出了定义"],
        "missing_points": ["k 参数的作用"],
        "followup": "k 越小会怎样？",
        "reference_sources": ["八股文.md"],
    }
    storage.save_interview_record("s1", 1, rec)
    storage.save_interview_state("s1", [{"role": "system", "content": "sys"}], None, False)

    state = storage.load_interview_state("s1")
    assert state is not None
    assert state["records"][0]["question"] == "什么是 RRF？"
    assert state["records"][0]["score"] == 60
    assert state["pending_question"] is None
    assert state["ended"] is False


def test_record_turn_upsert_keeps_order():
    _fresh_store()
    storage.save_interview_record("s1", 1, {"question": "Q1", "answer": "A1", "score": 50})
    storage.save_interview_record("s1", 2, {"question": "Q2", "answer": "A2", "score": 80})
    state = storage.load_interview_state("s1")
    assert [r["question"] for r in state["records"]] == ["Q1", "Q2"]


def test_clear_interview_removes_records_and_state():
    _fresh_store()
    storage.save_interview_record("s1", 1, {"question": "Q", "answer": "A", "score": 50})
    storage.save_interview_state("s1", [], "挂起题", True)

    storage.clear_interview("s1")

    assert storage.load_interview_state("s1") is None


def test_sessions_are_isolated():
    _fresh_store()
    storage.save_interview_record("s1", 1, {"question": "Q1", "answer": "A", "score": 50})
    storage.save_interview_record("s2", 1, {"question": "Q2", "answer": "A", "score": 90})

    s1 = storage.load_interview_state("s1")
    s2 = storage.load_interview_state("s2")
    assert s1["records"][0]["question"] == "Q1"
    assert s2["records"][0]["question"] == "Q2"


def test_interviewer_persist_skipped_without_session_id():
    """object.__new__ 旁路构造（无 _session_id）时不写库——单测旁路不该碰真实库。"""
    _fresh_store()
    from app.agents.interviewer import InterviewerAgent

    agent = object.__new__(InterviewerAgent)
    agent._records = []
    agent._history = []
    agent._pending_question = None
    agent._ended = False
    agent._record("Q", "A", {"score": 50, "missing_points": []}, [])
    agent._persist_state()  # _session_id 缺失 -> getattr 返回 "" -> 不落库

    assert storage.list_chat_sessions() == []
    assert storage.known_thread_ids() == set()
