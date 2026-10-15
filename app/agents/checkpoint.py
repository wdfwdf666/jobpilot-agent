"""LangGraph 检查点（持久化图状态）：人机协同 interrupt / resume 的地基。

为什么必须要有它（面试考点）：
- LangGraph 的 `interrupt()` 本质是"把当前状态存下来，然后抛出一个中断"。
  图如果没有 checkpointer，状态无处可存，中断抛出去就丢了，也就无从恢复；
- 所以"人工审核"落地不是加一行 `interrupt()`，而是三件套：
  带 checkpointer 编译 → 调用时传 thread_id → 用 `Command(resume=...)` 恢复同一条线程。
- 这里 thread_id 直接用 session_id：同一个会话的图状态（含"待审核的 JD 分析"）
  因此可以在**另一次请求、甚至另一个进程**里续上——这是多实例部署下"能接着走"的原理。

为什么用 SQLite 而不是内存 saver：
- `InMemorySaver` 进程一重启就全丢，无法演示"停一会儿再继续"；
- SQLite 单文件、零运维，配合已有的 `data/` 卷，容器重建也不丢（测试里仍用内存 saver）。

线程安全：`sqlite3.connect(..., check_same_thread=False)` 是必须的——FastAPI 的同步端点
跑在线程池里，每次请求可能落在不同线程；SqliteSaver 自身持有写锁，跨线程调用是安全的。
"""
import sqlite3
import threading

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import get_settings

_saver: SqliteSaver | None = None
_lock = threading.Lock()


def get_checkpointer() -> SqliteSaver:
    """进程级单例（与 retriever / vector store 同一套约定）。

    SqliteSaver 持有长连接，每次请求新建会让连接数随请求数增长；
    双检锁保证并发首访时也只建一条连接。
    """
    global _saver
    if _saver is None:
        with _lock:
            if _saver is None:
                settings = get_settings()
                settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(settings.checkpoint_path), check_same_thread=False)
                saver = SqliteSaver(conn)
                # setup() 建表。不调用时第一次 put 才会报错，且错误信息不指向这里
                # （真实踩坑：只有"表不存在"的 sqlite 报错），所以显式调用。
                saver.setup()
                _saver = saver
    return _saver
