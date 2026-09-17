"""检索器：V1 纯向量检索（第 1 周） -> V2 混合检索（第 2 周）。

演进叙事（面试核心考点）：
V1 上线后发现八股文里的专有名词（GIL、CAP 等）向量召回不准
-> V2 升级为 BM25 + 向量双路召回 -> RRF 融合 -> bge-reranker 重排。
TODO(V2): 安装 pyproject 的 [hybrid] 依赖后启用。
"""
import threading
from typing import Any

from app.rag.embeddings import get_embedding_client
from app.rag.vectorstore import get_vector_store


class Retriever:
    def __init__(self) -> None:
        # 复用进程级共享实例，而不是每次新建（并发下新建 chroma/openai 客户端会竞态）
        self._embedder = get_embedding_client()
        self._store = get_vector_store()

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """V1：纯向量检索。"""
        query_embedding = self._embedder.embed([query])[0]
        return self._store.query(query_embedding, top_k=top_k, category=category)

    # TODO(V2): hybrid_search(query) -> bm25 召回 + 向量召回 -> RRF 融合 -> rerank
    # def hybrid_search(self, query: str, top_k: int = 5) -> list[dict]:
    #     ...


# --- 进程级单例 -------------------------------------------------------------
# 检索器本身无状态，但它的构造会拉起 chroma 与 openai 客户端；放到线程池里
# 并发构造就是竞态。统一从这里取，构造只发生一次。
_RETRIEVER: Retriever | None = None
_RETRIEVER_LOCK = threading.Lock()


def get_retriever() -> Retriever:
    """取进程级共享的检索器。"""
    global _RETRIEVER
    if _RETRIEVER is None:
        with _RETRIEVER_LOCK:
            if _RETRIEVER is None:
                _RETRIEVER = Retriever()
    return _RETRIEVER
