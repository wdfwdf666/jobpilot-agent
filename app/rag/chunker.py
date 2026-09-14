"""语义分块：按标题/段落切分，块间 overlap 防止上下文断裂。

设计要点（面试考点）：
- 以"语义边界"（markdown 标题、空行分段）优先，而不是死板的固定长度硬切；
- overlap 保证跨块的知识点不至于被切断；
- 每块控制在 chunk_size 字符以内，超长段落再二次切分。
"""
import re

from app.config import get_settings


def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    settings = get_settings()
    chunk_size = chunk_size or settings.chunk_size
    overlap = overlap if overlap is not None else settings.chunk_overlap

    # 1. 先按语义边界切出"段落"：markdown 标题、空行
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#{1,4}\s)", text) if p.strip()]

    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        # 超长段落二次切分（带 overlap）
        while len(para) > chunk_size:
            chunks.append(para[:chunk_size])
            para = para[chunk_size - overlap:]
        if len(buffer) + len(para) + 1 <= chunk_size:
            buffer = f"{buffer}\n{para}".strip()
        else:
            if buffer:
                chunks.append(buffer)
            buffer = para
    if buffer:
        chunks.append(buffer)
    return chunks
