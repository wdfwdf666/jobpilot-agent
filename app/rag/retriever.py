"""检索器：V1 纯向量检索（第 1 周） -> V2 混合检索（第 2 周）。

演进叙事（面试核心考点）：
V1 上线后发现八股文里的专有名词（GIL、CAP 等）向量召回不准
-> V2 升级为 BM25 + 向量双路召回 -> RRF 融合 -> bge-reranker 重排。
TODO(V2): 安装 pyproject 的 [hybrid] 依赖后启用。
"""
from typing import Any

from app.rag.embeddings import EmbeddingClient
from app.rag.vectorstore import VectorStore


class Retriever:
    def __init__(self) -> None:
        self._embedder = EmbeddingClient()
        self._store = VectorStore()

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
