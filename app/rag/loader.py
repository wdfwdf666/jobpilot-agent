"""文档解析：pdf / docx / html / md / txt -> 纯文本。"""
import re
from pathlib import Path


def load_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix == ".docx":
        return _load_docx(path)
    if suffix in (".html", ".htm"):
        return _load_html(path)
    if suffix in (".md", ".txt", ".markdown"):
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"不支持的文件类型: {suffix}（支持 pdf/docx/html/md/txt）")


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _load_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


def _load_html(path: Path) -> str:
    """HTML 简历：剥标签取文本，块级标签转成换行以保住板块边界。"""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    # 先去掉 script/style，再把块级标签换成换行（否则整篇挤成一行，标题识别会失效）
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"</(p|div|li|tr|h[1-6]|section)>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", raw)
    # 实体与空白清理
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(entity, char)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)
