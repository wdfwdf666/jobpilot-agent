"""ChromaDB 封装：增量入库（内容哈希去重）+ 带 metadata 过滤的检索。

面试考点：
- 为什么用内容哈希做 doc_id？→ 同一文档重复添加时天然幂等，增量更新零成本。
- 为什么带 metadata？→ category/tags 过滤把检索范围缩小到正确领域，显著提升精度。
- 为什么必须是进程级单例？→ 见文件末尾 get_vector_store() 的注释（并发冷启动竞态）。
"""
import hashlib
import threading
import uuid
from typing import Any

import chromadb

from app.config import get_settings


def make_doc_id(text: str, source: str) -> str:
    """内容哈希：同一内容 + 同一来源 -> 同一 id，重复入库自动去重。"""
    return hashlib.sha1(f"{source}::{text}".encode("utf-8")).hexdigest()


class VectorStore:
    def __init__(self) -> None:
        settings = get_settings()
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(settings.chroma_path))
        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        source: str = "manual",
        category: str = "通用",
        tags: list[str] | None = None,
    ) -> dict[str, int]:
        """批量入库，返回 {added, skipped}。已存在（哈希相同）的块跳过。"""
        tags = tags or []
        metadatas: list[dict[str, Any]] = []
        ids: list[str] = []
        for chunk in chunks:
            doc_id = make_doc_id(chunk, source)
            metadatas.append({
                "source": source,
                "category": category,
                "tags": ",".join(tags),  # chroma metadata 不支持 list，用逗号串
                "chunk_uuid": uuid.uuid4().hex[:8],
            })
            ids.append(doc_id)

        existing = set(self._collection.get(ids=ids)["ids"])
        new_ids, new_chunks, new_embeddings, new_metadatas = [], [], [], []
        for doc_id, chunk, emb, meta in zip(ids, chunks, embeddings, metadatas):
            if doc_id in existing:
                continue
            new_ids.append(doc_id)
            new_chunks.append(chunk)
            new_embeddings.append(emb)
            new_metadatas.append(meta)

        if new_ids:
            self._collection.add(
                ids=new_ids, documents=new_chunks, embeddings=new_embeddings, metadatas=new_metadatas
            )
        return {"added": len(new_ids), "skipped": len(ids) - len(new_ids)}

    def query(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """向量检索，可选 category 过滤。"""
        where = {"category": category} if category else None
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self._collection.count() or 1),
            where=where,
        )
        hits: list[dict[str, Any]] = []
        for i, doc_id in enumerate(result["ids"][0]):
            hits.append({
                "id": doc_id,
                "text": result["documents"][0][i],
                "metadata": result["metadatas"][0][i],
                "distance": result["distances"][0][i],
            })
        return hits

    def count(self) -> int:
        return self._collection.count()

    def delete_by_source(self, source: str) -> None:
        self._collection.delete(where={"source": source})


# --- 进程级单例 -------------------------------------------------------------
# 为什么必须单例（真实踩坑，面试可讲）：
# uvicorn 把同步端点丢进线程池，多个请求是**真正并行**的。而
# chromadb.PersistentClient(...) 的构造不只是打开文件——它要初始化 Rust bindings，
# 再校验 tenant/database。当"首次构造"发生在请求线程里、且多个线程同时执行，
# 后到的线程会读到还没注册好的 bindings：
#     AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
# 继而被包装成：
#     ValueError: Could not connect to tenant default_tenant
# 直接 500。症状极具迷惑性——单请求永远正常，一并发就偶发失败。
# 同类问题还有 openai SDK 的 resources 子模块惰性导入（见 app/warmup.py）。
_SINGLETON: VectorStore | None = None
_SINGLETON_LOCK = threading.Lock()


def get_vector_store() -> VectorStore:
    """取进程级共享的向量库实例；构造只发生一次（双检锁）。"""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = VectorStore()
    return _SINGLETON
