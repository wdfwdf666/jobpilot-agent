"""启动预热：把"惰性 import + 重客户端初始化"从中并发的请求线程提前到单线程启动阶段。

这是本项目踩到过的最隐蔽的一个坑，值得写进面试答案：

症状
----
单请求永远正常，一并发就偶发 500：
- `deadlock detected by _ModuleLock('openai.resources.embeddings')`
- `ValueError: Could not connect to tenant default_tenant`

根因（三件事叠在一起）
--------------------
1. uvicorn 把**同步端点**丢进线程池（anyio worker thread），多个请求真正并行
   执行——不是"看起来并发"，是真并行。
2. 新版 openai SDK 对 resources 子模块做**惰性导入**：`from openai import OpenAI`
   只拿到类；`client.chat` / `client.embeddings` 首次被访问时才 import
   `openai.resources.chat` / `openai.resources.embeddings`。
3. `chromadb.PersistentClient(...)` 的构造要初始化 Rust bindings 并校验
   tenant/database，不是单纯打开一个目录。

于是"首次初始化"发生在了请求线程里。两个线程同时做，且 import 顺序不同
（embedding 链路 vs chat 链路）就会形成模块锁环；chroma 那边则表现为 bindings
还没注册好就被另一个线程读取。

解法（两条一起上）
----------------
- **预热**：启动时在主线程把这些初始化串行跑一遍（本模块）。
- **单例**：请求路径只取已就绪的进程级单例，绝不重复构造
  （见 vectorstore / embeddings / retriever / llm）。

副作用是首请求延迟也更低了：用户不用再等 chroma 冷启动。
"""
import logging
from typing import Any

logger = logging.getLogger("jobpilot.warmup")


def warmup() -> dict[str, Any]:
    """串行预加载。任何一步失败都不阻断启动，只记日志——预热是优化，不是前置条件。"""
    report: dict[str, Any] = {}

    # 1) openai 惰性子模块：提前 import，避免首次访问落在并发请求线程里
    try:
        import openai.resources.chat.completions  # noqa: F401
        import openai.resources.embeddings  # noqa: F401

        report["openai_submodules"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["openai_submodules"] = f"skip: {exc}"

    # 2) 向量库：一定会用到，且构造最重（Rust bindings + tenant 校验）
    try:
        from app.rag.vectorstore import get_vector_store

        store = get_vector_store()
        report["vector_store"] = f"ok ({store.count()} chunks)"
    except Exception as exc:  # noqa: BLE001
        report["vector_store"] = f"fail: {exc}"

    # 3) Embedding / LLM 客户端：需要 API Key，未配置时跳过而不是让服务起不来
    try:
        from app.rag.embeddings import get_embedding_client

        get_embedding_client()
        report["embedding_client"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["embedding_client"] = f"skip: {exc}"

    try:
        from app.agents.llm import get_llm

        get_llm()
        report["llm_client"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["llm_client"] = f"skip: {exc}"

    # 4) 检索器（依赖 2、3 都就绪）
    try:
        from app.rag.retriever import get_retriever

        get_retriever()
        report["retriever"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["retriever"] = f"skip: {exc}"

    logger.info("warmup: %s", report)
    return report
