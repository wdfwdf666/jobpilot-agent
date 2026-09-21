"""混合检索：BM25（稀疏）+ 向量（稠密）双路召回 -> RRF 融合。

为什么混合（面试核心叙事）：
- 纯向量上线后发现八股文里的专有名词（GIL、CAP、MVCC）召回不稳——
  embedding 对"字面精确匹配"信号弱，同义改写能力强；
- BM25 相反：词面精确命中强，但不懂同义改写（问"多线程为什么慢"
  不会命中写着 "GIL" 的段落）；
- 两路互补 -> 各自召回 top-N -> RRF 融合。

为什么用 RRF 而不是分数加权：
BM25 分数无上界、余弦距离是距离不是相似度，两路分数量纲不可比，
直接加权需要调归一化参数；RRF 只用**排名**融合：score = Σ 1/(k + rank)，
k=60（Cormack et al. 2009 论文常用值），零调参、对异常分数鲁棒。
"""
import threading
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from app.rag.vectorstore import VectorStore

RRF_K = 60  # 缺省值，实际取 Settings.rrf_k（可用 .env / 环境变量覆盖做网格搜索）
# 每路召回数量：要大于最终 top_k，给融合留出合并空间（实际取 Settings.recall_per_channel）
RECALL_PER_CHANNEL = 20


def tokenize(text: str) -> list[str]:
    """中文分词：jieba 切词 + 英文转小写，过滤空白和单字符标点。"""
    return [
        t.lower()
        for t in jieba.lcut(text)
        if t.strip() and not all(not ch.isalnum() for ch in t)
    ]


class BM25Index:
    """对向量库全量块维护 BM25 索引。

    库很小（几十~几千块），全量重建毫秒级，所以用"块数变化即重建"的
    惰性策略，避免监听每一条增删的复杂度。线程安全：重建在锁内完成。
    """

    def __init__(self, store: VectorStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._chunks: list[dict[str, Any]] = []
        self._bm25: BM25Okapi | None = None
        self._built_count = -1

    def search(
        self, query: str, top_k: int, category: str | None = None
    ) -> list[dict[str, Any]]:
        """BM25 召回。category 过滤在建了全量索引后于子集上重排。"""
        self._ensure_index()
        with self._lock:
            if self._bm25 is None or not self._chunks:
                return []
            candidates = [
                (i, c)
                for i, c in enumerate(self._chunks)
                if category is None or c["metadata"].get("category") == category
            ]
            if not candidates:
                return []
            scores = self._bm25.get_scores(tokenize(query))
            ranked = sorted(
                candidates, key=lambda pair: scores[pair[0]], reverse=True
            )[:top_k]
            return [dict(c, bm25_score=scores[i]) for i, c in ranked]

    def _ensure_index(self) -> None:
        count = self._store.count()
        if count == self._built_count and self._bm25 is not None:
            return
        with self._lock:
            count = self._store.count()  # 双检：等锁期间可能已被别的线程重建
            if count == self._built_count and self._bm25 is not None:
                return
            self._chunks = self._store.get_all_chunks()
            corpus = [tokenize(c["text"]) for c in self._chunks]
            self._bm25 = BM25Okapi(corpus) if corpus else None
            self._built_count = count


def rrf_fuse(
    *ranked_lists: list[dict[str, Any]], top_k: int, k: int | None = None
) -> list[dict[str, Any]]:
    """RRF 融合多路召回结果，按 id 去重合并，metadata/text 取首个非空版本。

    k 缺省取配置（RRF_K=60）。融合只依赖名次，所以两路的分数量纲不一致也没关系。
    """
    from app.config import get_settings

    k = k or get_settings().rrf_k
    scores: dict[str, float] = {}
    hits: dict[str, dict[str, Any]] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked):
            hid = hit["id"]
            scores[hid] = scores.get(hid, 0.0) + 1.0 / (k + rank + 1)
            if hid not in hits:
                hits[hid] = hit
    fused_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_k]
    out = []
    for hid in fused_ids:
        fused = dict(hits[hid])
        fused["rrf_score"] = round(scores[hid], 6)
        out.append(fused)
    return out
