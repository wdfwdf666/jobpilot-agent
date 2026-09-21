"""检索器：V1 纯向量检索 -> V2 混合检索（BM25 + 向量 + RRF 融合）。

演进叙事（面试核心考点）：
V1 上线后发现八股文里的专有名词（GIL、CAP 等）向量召回不准
-> V2 升级为 BM25 + 向量双路召回 + RRF 融合（见 app/rag/hybrid.py）。
两种模式通过 RETRIEVAL_MODE 配置切换，评测脚本据此前后对比。

bge-reranker 重排做成可选后续项：CPU 推理慢、依赖重（sentence-transformers），
当前库规模下 RRF 融合已显著优于单路，rerank 留给规模化后再上。
"""
import threading
from typing import Any

from app.config import get_settings
from app.rag.embeddings import get_embedding_client
from app.rag.hybrid import BM25Index, rrf_fuse
from app.rag.vectorstore import get_vector_store


class Retriever:
    def __init__(self) -> None:
        # 复用进程级共享实例，而不是每次新建（并发下新建 chroma/openai 客户端会竞态）
        self._embedder = get_embedding_client()
        self._store = get_vector_store()
        self._bm25 = BM25Index(self._store)

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        """检索入口。mode 缺省取配置（RETRIEVAL_MODE，默认 hybrid）。

        hybrid：两路各召回 recall_per_channel 条 -> RRF 融合取 top_k。
        vector：纯向量（V1 行为，用于评测对比基线）。
        """
        mode = (mode or get_settings().retrieval_mode).lower()
        if mode != "hybrid":
            query_embedding = self._embedder.embed([query])[0]
            return self._store.query(query_embedding, top_k=top_k, category=category)

        recall_n = max(top_k, get_settings().recall_per_channel)
        vec_hits = self._store.query(
            self._embedder.embed([query])[0],
            top_k=recall_n,
            category=category,
        )
        bm25_hits = self._bm25.search(query, recall_n, category)
        # 向量命中的 distance 保留；BM25 独有的命中没有 distance（前端不依赖它）
        return rrf_fuse(vec_hits, bm25_hits, top_k=top_k)


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
