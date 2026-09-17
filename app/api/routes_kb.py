"""知识库管理接口：添加知识（粘贴/上传）、检索测试、统计。"""
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import get_settings
from app.rag.chunker import chunk_text
from app.rag.embeddings import EmbeddingClient
from app.rag.loader import load_text
from app.rag.resume_parser import parse_resume
from app.rag.vectorstore import VectorStore

router = APIRouter(prefix="/kb", tags=["knowledge-base"])

# 简历走独立解析策略的类别名
RESUME_CATEGORY = "简历素材"


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
    """上传 md/txt/pdf/docx/html 文件入库。

    category=简历素材 时自动走简历解析器（按板块切分 + 板块标签），
    其他类别按通用文档分块。这是"同一入口、不同解析策略"的路由式设计。
    """
    settings = get_settings()
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    # Windows 坑：临时文件句柄未关闭时 shutil.move 会报 WinError 32（文件被占用）。
    # 直接读字节落盘，避免移动打开中的文件；取 filename 部分防止路径穿越。
    filename = Path(file.filename or "upload").name
    dest = settings.uploads_path / filename
    dest.write_bytes(await file.read())
    text = load_text(dest)

    if category == RESUME_CATEGORY:
        profile = parse_resume(text)
        blocks = profile.to_blocks()
        if not blocks:
            raise HTTPException(400, "简历内容为空或无法解析")
        embeddings = EmbeddingClient().embed([b["text"] for b in blocks])
        result = VectorStore().add_chunks(
            [b["text"] for b in blocks], embeddings, source=filename,
            category=category, tags=sorted({b["section"] for b in blocks}),
        )
        result["sections"] = [s.name for s in profile.sections]
        result["structured"] = profile.structured
        return result

    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(400, "文本过短或无法分块")
    embeddings = EmbeddingClient().embed(chunks)
    return VectorStore().add_chunks(chunks, embeddings, source=filename,
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
