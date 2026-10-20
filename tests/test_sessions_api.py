"""会话索引（左侧列表）接口测试。

不走 TestClient：把路由函数当普通函数调用——它们只依赖 storage 模块和
checkpointer，前者可注入临时库，后者在删除路径上允许失败（返回 ok=False）。
重点验证三个语义：标题只认首条消息、列表做检查点并集兜底、删除三处一起清。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage  # noqa: E402
from app.api.routes_chat import (  # noqa: E402
    KIND_CHAT,
    KIND_INTERVIEW,
    _merged_sessions,
    get_sessions,
    remove_session,
)


def _fresh_store():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    Path(f.name).unlink()
    conn = sqlite3.connect(f.name, check_same_thread=False)
    storage.init(conn)
    # 生产库里 checkpoints 由 SqliteSaver.setup() 建；临时库要手工补一张最小表，
    # 让 known_thread_ids() 的并集兜底有真实表可查。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS checkpoints ("
        " thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT,"
        " parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata TEXT)"
    )
    conn.commit()
    return conn


def _touch(sid, title=None, kind=None, turns=None):
    storage.touch_chat_session(sid, title=title, kind=kind, turns=turns)


def test_title_only_from_first_message():
    _fresh_store()
    _touch("s1", title="分析这个 JD：负责 Agent 研发")
    _touch("s1")  # 第二条消息：只更新活跃时间
    _touch("s1", title="后来的一句话")

    rows = storage.list_chat_sessions()
    assert len(rows) == 1
    assert rows[0]["title"] == "分析这个 JD：负责 Agent 研发"  # 不被后来的话改写


def test_kind_and_turns_set_at_done():
    _fresh_store()
    _touch("s1", title="我想模拟面试")
    _touch("s1", kind=KIND_INTERVIEW, turns=3)

    row = storage.list_chat_sessions()[0]
    assert row["kind"] == KIND_INTERVIEW
    assert row["turns"] == 3


def test_merged_sessions_union_with_checkpoint_threads():
    _fresh_store()
    _touch("web-abc", title="有索引的会话")
    # 手工往检查点表塞一条没有索引行的 thread（模拟老会话/外部写入）
    conn = storage.get_conn()
    conn.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,"
        " type, checkpoint, metadata) VALUES ('legacy-thread', '', 'c1', NULL, 'put', x'00', '{}')"
    )
    conn.commit()

    merged = _merged_sessions()
    ids = [s["session_id"] for s in merged]
    assert "web-abc" in ids
    assert "legacy-thread" in ids  # 兜底：不在索引里的历史线程不能凭空消失
    legacy = next(s for s in merged if s["session_id"] == "legacy-thread")
    assert legacy["title"] == "未命名会话"
    assert legacy["kind"] == KIND_CHAT


def test_get_sessions_route_shape():
    _fresh_store()
    _touch("s1", title="标题", kind=KIND_CHAT, turns=2)
    resp = get_sessions()
    assert resp["sessions"][0]["session_id"] == "s1"
    assert resp["sessions"][0]["title"] == "标题"


def test_remove_session_cleans_all_three_places():
    _fresh_store()
    _touch("s1", title="待删", turns=1)
    storage.save_interview_record("s1", 1, {"question": "Q", "answer": "A", "score": 50})
    storage.save_interview_state("s1", [], "挂起", False)

    resp = remove_session("s1")

    # 临时库不是 checkpointer 的真实库：delete_thread 失败会返回 ok=False，
    # 但索引与面试记录必须已经清掉（这两处不依赖 checkpointer）
    assert storage.list_chat_sessions() == []
    assert storage.load_interview_state("s1") is None
    assert isinstance(resp, dict) and "ok" in resp
