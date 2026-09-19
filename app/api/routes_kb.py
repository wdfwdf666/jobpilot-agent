"""知识库管理接口：添加知识（粘贴/上传）、检索测试、统计、文档管理（查/改/删）。"""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import get_settings
from app.rag.chunker import chunk_text
from app.rag.embeddings import get_embedding_client
from app.rag.loader import load_text
from app.rag.resume_parser import parse_resume
from app.rag.vectorstore import get_vector_store

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


class ChunkUpdateRequest(BaseModel):
    text: str = Field(min_length=1)


@router.post("/documents")
def add_document(req: IngestRequest) -> dict:
    """粘贴文本直接入库。内容哈希去重：重复添加自动 skip。"""
    chunks = chunk_text(req.text)
    if not chunks:
        raise HTTPException(400, "文本过短或无法分块")
    # 每次粘贴生成独立 source，才能在管理面板里单独删除/修改某一次粘贴
    source = req.source
    if source == "manual":
        source = f"手动粘贴-{datetime.now():%m%d-%H%M%S}"
    embeddings = get_embedding_client().embed(chunks)
    return get_vector_store().add_chunks(chunks, embeddings, source=source,
                                         category=req.category, tags=req.tags)


def _ingest_file(filename: str, data: bytes, category: str) -> dict:
    """单文件入库（上传/批量上传共用）。

    category=简历素材 时自动走简历解析器（按板块切分 + 板块标签），
    其他类别按通用文档分块。这是"同一入口、不同解析策略"的路由式设计。
    """
    settings = get_settings()
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    # Windows 坑：临时文件句柄未关闭时 shutil.move 会报 WinError 32（文件被占用）。
    # 直接读字节落盘，避免移动打开中的文件；取 filename 部分防止路径穿越。
    safe_name = Path(filename or "upload").name
    dest = settings.uploads_path / safe_name
    dest.write_bytes(data)
    text = load_text(dest)

    if category == RESUME_CATEGORY:
        profile = parse_resume(text)
        blocks = profile.to_blocks()
        if not blocks:
            raise HTTPException(400, "简历内容为空或无法解析")
        embeddings = get_embedding_client().embed([b["text"] for b in blocks])
        result = get_vector_store().add_chunks(
            [b["text"] for b in blocks], embeddings, source=safe_name,
            category=category, tags=sorted({b["section"] for b in blocks}),
        )
        result["sections"] = [s.name for s in profile.sections]
        result["structured"] = profile.structured
        return result

    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(400, "文本过短或无法分块")
    embeddings = get_embedding_client().embed(chunks)
    return get_vector_store().add_chunks(chunks, embeddings, source=safe_name,
                                         category=category)


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), category: str = "通用") -> dict:
    """上传单个文件入库（批量请用 /upload-batch）。"""
    return _ingest_file(file.filename or "upload", await file.read(), category)


@router.post("/upload-batch")
async def upload_documents_batch(
    files: list[UploadFile] = File(...), category: str = "通用"
) -> dict:
    """批量上传入库（category 走 query string，与 /upload 保持一致）。

    逐个文件独立处理：某个文件解析失败只记录错误，不中断整批。
    返回 per-file 结果，前端据此展示「成功 N / 失败 M」明细。
    """
    if not files:
        raise HTTPException(400, "未选择任何文件")
    results = []
    for f in files:
        name = f.filename or "upload"
        try:
            r = _ingest_file(name, await f.read(), category)
            results.append({
                "filename": name, "ok": True,
                "added": r["added"], "skipped": r["skipped"],
            })
        except HTTPException as exc:
            results.append({"filename": name, "ok": False, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001 —— 单文件异常不拖垮整批
            results.append({"filename": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    ok_count = sum(1 for r in results if r["ok"])
    return {
        "results": results,
        "ok_count": ok_count,
        "fail_count": len(results) - ok_count,
        "added": sum(r.get("added", 0) for r in results if r["ok"]),
        "skipped": sum(r.get("skipped", 0) for r in results if r["ok"]),
    }


@router.post("/search")
def search(req: SearchRequest) -> dict:
    hits = get_vector_store().query(
        get_embedding_client().embed([req.query])[0], top_k=req.top_k, category=req.category
    )
    return {"hits": hits}


@router.get("/stats")
def stats() -> dict:
    return {"total_chunks": get_vector_store().count()}


# --- 文档管理（查 / 改 / 删） ---------------------------------------------------

@router.get("/documents")
def list_documents() -> dict:
    """列出库内全部文档（按 source+category 分组）及各自块数。"""
    return {"documents": get_vector_store().list_documents()}


@router.get("/chunks")
def get_chunks(source: str, category: str | None = None) -> dict:
    """查看某文档下的全部块（原文 + 元数据）。"""
    return {"chunks": get_vector_store().get_chunks(source, category)}


@router.delete("/documents")
def delete_document(source: str, category: str | None = None) -> dict:
    """删除整个文档；带 category 时只删该分类下的同名文档。"""
    removed = get_vector_store().delete_by_source(source, category)
    if removed == 0:
        raise HTTPException(404, "未找到该文档")
    return {"ok": True, "removed": removed}


@router.delete("/chunks/{chunk_id}")
def delete_chunk(chunk_id: str) -> dict:
    if not get_vector_store().delete_chunk(chunk_id):
        raise HTTPException(404, "块不存在（可能已被删除）")
    return {"ok": True}


@router.put("/chunks/{chunk_id}")
def update_chunk(chunk_id: str, req: ChunkUpdateRequest) -> dict:
    """修改块文本：重新向量化入库，元数据（来源/分类）原样保留。"""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "文本不能为空")
    embedding = get_embedding_client().embed([text])[0]
    result = get_vector_store().update_chunk_text(chunk_id, text, embedding)
    if not result["updated"]:
        raise HTTPException(404, "块不存在（可能已被删除）")
    return result
