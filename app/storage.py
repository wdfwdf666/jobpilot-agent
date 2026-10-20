"""本地业务存储：面试评分记录 + 会话索引，与检查点共用同一个 SQLite 文件。

为什么落在这里（问题背景）：D12.3 之前，面试的逐题评分、面试官上下文都只活在
进程内存（`_INTERVIEWERS` 注册表），后端一重启就丢，还有 50 场的静默淘汰上限——
用户不会知道自己的面试报告已经没了。本模块把"业务侧记录"落进检查点同一个库，
口径统一成：图状态与业务记录同库、同生命周期。

为什么复用 checkpointer 的连接（面试考点）：
- SqliteSaver 实例直接暴露 `saver.conn`（实测同一对象），拿到它就拿到了同一条长连接；
- 同一个 SQLite 文件开两条连接去写，会撞 `database is locked`——复用一条连接
  从根上消掉这个问题，而不是靠 busy_timeout 祈祷；
- 读多写少、单进程部署（uvicorn 单 worker），一条连接 + 模块级写锁足够；
  真要上多实例，这里就是该换成 Redis/Postgres 的边界（README 已注明）。

测试注入：`init(conn)` 可以换掉连接（内存库/临时库），生产代码不感知。
"""
import json
import sqlite3
import threading
from datetime import datetime

from app.agents.checkpoint import get_checkpointer

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_records (
    session_id TEXT NOT NULL,
    turn       INTEGER NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    score      INTEGER NOT NULL,
    payload    TEXT NOT NULL,          -- 完整评分记录 JSON（对/错要点、追问、参考来源）
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, turn)
);
CREATE TABLE IF NOT EXISTS interview_sessions (
    session_id       TEXT PRIMARY KEY,
    history          TEXT NOT NULL,    -- 面试官的对话历史 JSON（含 system 开场）
    pending_question TEXT,
    ended            INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT '对话',
    turns      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
    ON chat_sessions (updated_at DESC);
"""


def init(conn: sqlite3.Connection) -> None:
    """注入连接并建表（测试用；生产走 get_conn 的懒加载路径）。"""
    global _conn
    with _lock:
        _conn = conn
        conn.executescript(_SCHEMA)
        conn.commit()


def get_conn() -> sqlite3.Connection:
    """业务表与检查点共用的唯一连接：来自 SqliteSaver 单例。

    不用双检锁套住建表——get_checkpointer() 自己是单例，这里只做一次幂等 DDL。
    """
    global _conn
    if _conn is None:
        conn = get_checkpointer().conn
        with _lock:
            if _conn is None:
                conn.executescript(_SCHEMA)
                conn.commit()
                _conn = conn
    return _conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- 面试：逐题评分记录 ------------------------------------------------------

def save_interview_record(session_id: str, turn: int, record: dict) -> None:
    with _lock:
        get_conn().execute(
            "INSERT OR REPLACE INTO interview_records"
            " (session_id, turn, question, answer, score, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                turn,
                record.get("question", ""),
                record.get("answer", ""),
                int(record.get("score", 0)),
                json.dumps(record, ensure_ascii=False),
                _now(),
            ),
        )
        get_conn().commit()


def load_interview_state(session_id: str) -> dict | None:
    """读回一场面试的完整状态（评分记录 + 面试官上下文）。

    两类行都可能单独存在（评分先落、状态行还没写），任一非空即返回；
    都为空才算"这个会话没有面试数据"。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT history, pending_question, ended FROM interview_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    records = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT payload FROM interview_records WHERE session_id = ? ORDER BY turn",
            (session_id,),
        )
    ]
    if row is None and not records:
        return None
    return {
        "records": records,
        "history": json.loads(row[0]) if row else [],
        "pending_question": row[1] if row else None,
        "ended": bool(row[2]) if row else False,
    }


def save_interview_state(
    session_id: str, history: list[dict], pending_question: str | None, ended: bool
) -> None:
    with _lock:
        get_conn().execute(
            "INSERT OR REPLACE INTO interview_sessions"
            " (session_id, history, pending_question, ended, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, json.dumps(history, ensure_ascii=False),
             pending_question, int(ended), _now()),
        )
        get_conn().commit()


def clear_interview(session_id: str) -> None:
    """开新一场 / 删除会话：评分记录与面试官上下文一起清。"""
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM interview_records WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM interview_sessions WHERE session_id = ?", (session_id,))
        conn.commit()


# --- 会话索引（左侧列表） ----------------------------------------------------

def touch_chat_session(
    session_id: str,
    title: str | None = None,
    kind: str | None = None,
    turns: int | None = None,
) -> None:
    """存在则按字段更新，不存在则建。

    两个写入时机互不知情，用条件更新保住已写入的字段：
    - title：**首条消息优先**（会话标题不该被后来的话改写），只在为空时写入；
    - kind / turns：done 时写，正常覆盖。
    列表查询时再给空标题兜底成「未命名会话」。
    """
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO chat_sessions (session_id, title, kind, turns, created_at, updated_at)"
            " VALUES (?, COALESCE(?, ''), '对话', 0, ?, ?)"
            " ON CONFLICT(session_id) DO NOTHING",
            (session_id, title, _now(), _now()),
        )
        conn.execute(
            "UPDATE chat_sessions SET"
            " title = CASE WHEN COALESCE(title, '') = '' THEN COALESCE(?, title) ELSE title END,"
            " kind = COALESCE(?, kind),"
            " turns = COALESCE(?, turns),"
            " updated_at = ?"
            " WHERE session_id = ?",
            (title, kind, turns, _now(), session_id),
        )
        conn.commit()


def list_chat_sessions(limit: int = 50) -> list[dict]:
    """最近 limit 个会话，按最近活跃倒序。"""
    rows = get_conn().execute(
        "SELECT session_id, COALESCE(NULLIF(title, ''), '未命名会话'), kind, turns,"
        " created_at, updated_at"
        " FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "session_id": r[0], "title": r[1], "kind": r[2],
            "turns": r[3], "created_at": r[4], "updated_at": r[5],
        }
        for r in rows
    ]


def known_thread_ids() -> set[str]:
    """检查点里出现过的 thread_id（无索引行的兜底：历史线程不该在列表里凭空消失）。"""
    try:
        rows = get_conn().execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
    except sqlite3.OperationalError:  # 表还没建（全新库）不算错
        return set()
    return {r[0] for r in rows if r[0]}


def delete_chat_session(session_id: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM interview_records WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM interview_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
