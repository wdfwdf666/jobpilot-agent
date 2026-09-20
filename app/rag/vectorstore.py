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

    def get_all_chunks(self) -> list[dict[str, Any]]:
        """取全库块（id/text/metadata），供 BM25 建索引等全量扫描场景。"""
        result = self._collection.get(include=["documents", "metadatas"])
        return [
            {
                "id": result["ids"][i],
                "text": (result["documents"] or [])[i],
                "metadata": (result["metadatas"] or [])[i],
            }
            for i in range(len(result["ids"]))
        ]

    def count(self) -> int:
        return self._collection.count()

    # --- 文档管理 ---------------------------------------------------------------

    def list_documents(self) -> list[dict[str, Any]]:
        """按 (source, category) 分组列出库内文档及块数，供管理面板展示。"""
        result = self._collection.get(include=["metadatas"])
        docs: dict[tuple[str, str], dict[str, Any]] = {}
        for meta in result["metadatas"] or []:
            source = str(meta.get("source") or "unknown")
            category = str(meta.get("category") or "通用")
            key = (source, category)
            entry = docs.setdefault(key, {"source": source, "category": category, "chunks": 0})
            entry["chunks"] += 1
        return sorted(docs.values(), key=lambda d: (d["category"], d["source"]))

    def get_chunks(self, source: str, category: str | None = None) -> list[dict[str, Any]]:
        """取某文档（source [+ category]）下的全部块，含原文与元数据。"""
        where: dict[str, Any] = {"source": source}
        if category:
            where = {"$and": [{"source": source}, {"category": category}]}
        result = self._collection.get(where=where, include=["documents", "metadatas"])
        return [
            {
                "id": result["ids"][i],
                "text": (result["documents"] or [])[i],
                "metadata": (result["metadatas"] or [])[i],
            }
            for i in range(len(result["ids"]))
        ]

    def delete_by_source(self, source: str, category: str | None = None) -> int:
        """删除整个文档；带 category 时只删该分类下的同名文档。"""
        where: dict[str, Any] = {"source": source}
        if category:
            where = {"$and": [{"source": source}, {"category": category}]}
        before = self._collection.count()
        self._collection.delete(where=where)
        return before - self._collection.count()

    def delete_chunk(self, chunk_id: str) -> bool:
        """删除单个块。id 不存在时返回 False（幂等保护）。"""
        if not self._collection.get(ids=[chunk_id])["ids"]:
            return False
        self._collection.delete(ids=[chunk_id])
        return True

    def update_chunk_text(
        self, chunk_id: str, new_text: str, embedding: list[float]
    ) -> dict[str, Any]:
        """修改块文本：旧 id 删除 -> 新文本重新向量化入库（内容哈希 id 随内容变化）。

        元数据（source/category/tags）原样保留，所以改完仍归在同一文档下。
        若新文本与其他块哈希撞车，则只删不增（天然去重）。
        """
        old = self._collection.get(ids=[chunk_id], include=["metadatas"])
        if not old["ids"]:
            return {"updated": 0}
        meta = old["metadatas"][0]
        source = str(meta.get("source") or "manual")
        self._collection.delete(ids=[chunk_id])
        new_id = make_doc_id(new_text, source)
        deduped = bool(self._collection.get(ids=[new_id])["ids"])
        if not deduped:
            self._collection.add(
                ids=[new_id], documents=[new_text], embeddings=[embedding], metadatas=[meta]
            )
        return {"updated": 1, "deduped": deduped}


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
