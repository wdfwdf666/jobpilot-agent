"""并发安全回归测试：重客户端必须是进程级单例。

背景（真踩过的坑）：uvicorn 把同步端点丢进线程池，多个请求真正并行。若
chromadb / openai 客户端是"每请求新建"，冷启动时多个线程同时构造就会互相踩：
- AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
  -> ValueError: Could not connect to tenant default_tenant
- deadlock detected by _ModuleLock('openai.resources.embeddings')
症状是"单请求正常、一并发偶发 500"。这组测试锁住这个约定，防止有人改回去。
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_vector_store_is_singleton():
    from app.rag.vectorstore import get_vector_store

    assert get_vector_store() is get_vector_store()


def test_vector_store_singleton_under_concurrency():
    """多线程同时首次获取，必须拿到同一个实例（且不抛异常）。"""
    from app.rag import vectorstore

    # 重置单例，模拟冷启动时多个线程同时首次进入。
    # 测完必须还原：否则后续测试（如 test_retriever_reuses_shared_clients）里
    # get_vector_store() 会拿到新实例，与早前创建的 Retriever 内部持有的旧实例身份不符。
    old = vectorstore._SINGLETON
    vectorstore._SINGLETON = None
    try:
        results: list[object] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            try:
                barrier.wait(timeout=10)
                results.append(vectorstore.get_vector_store())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"并发获取单例报错：{errors}"
        assert len(results) == 8
        assert len({id(r) for r in results}) == 1, "并发下产生了多个实例"
    finally:
        vectorstore._SINGLETON = old


def test_llm_client_is_singleton():
    from app.agents.llm import get_llm

    try:
        client = get_llm()
    except RuntimeError:
        pytest.skip("未配置 LLM_API_KEY，跳过")
    assert client is get_llm()


def test_embedding_client_is_singleton():
    from app.rag.embeddings import get_embedding_client

    try:
        client = get_embedding_client()
    except RuntimeError:
        pytest.skip("未配置 API Key，跳过")
    assert client is get_embedding_client()


def test_retriever_reuses_shared_clients():
    from app.rag import retriever as retriever_mod
    from app.rag.vectorstore import get_vector_store

    try:
        r = retriever_mod.get_retriever()
    except RuntimeError:
        pytest.skip("未配置 API Key，跳过")
    assert r is retriever_mod.get_retriever()
    # 检索器内部复用的必须是同一个向量库实例（而不是自己新建了一个）
    assert r._store is get_vector_store()
