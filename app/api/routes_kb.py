"""知识库管理接口：添加知识（粘贴/上传）、检索测试、统计。"""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import get_settings
from app.rag.chunker import chunk_text
from app.rag.embeddings import EmbeddingClient
from app.rag.loader import load_text
from app.rag.vectorstore import VectorStore

router = APIRouter(prefix="/kb", tags=["knowledge-base"])


class IngestRequest(BaseModel):
    text: str = Field(min_length=1)
    source: str = "manual"
    category: str = Field(default="通用", description="八股文/项目笔记/行业认知/简历素材")
    tags: list[str] = []


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: str | None = None


@router.post("/documents")
def add_document(req: IngestRequest) -> dict:
    """粘贴文本直接入库。内容哈希去重：重复添加自动 skip。"""
    chunks = chunk_text(req.text)
    if not chunks:
        raise HTTPException(400, "文本过短或无法分块")
    embeddings = EmbeddingClient().embed(chunks)
    return VectorStore().add_chunks(chunks, embeddings, source=req.source,
                                    category=req.category, tags=req.tags)


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), category: str = "通用") -> dict:
    """上传 md/txt/pdf/docx 文件入库。"""
    settings = get_settings()
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    dest = settings.uploads_path / file.filename  # type: ignore[arg-type]
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        shutil.move(tmp.name, dest)
    text = load_text(dest)
    chunks = chunk_text(text)
    embeddings = EmbeddingClient().embed(chunks)
    return VectorStore().add_chunks(chunks, embeddings, source=file.filename or "upload",
                                    category=category)


@router.post("/search")
def search(req: SearchRequest) -> dict:
    hits = VectorStore().query(
        EmbeddingClient().embed([req.query])[0], top_k=req.top_k, category=req.category
    )
    return {"hits": hits}


@router.get("/stats")
def stats() -> dict:
    store = VectorStore()
    return {"total_chunks": store.count()}
