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
        return _strip_html_noise(path.read_text(encoding="utf-8", errors="ignore"))
    raise ValueError(f"不支持的文件类型: {suffix}（支持 pdf/docx/html/md/txt）")


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _load_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


def _strip_html_noise(text: str, min_tags: int = 20) -> str:
    """md/txt 里内嵌网页标记（网页导出成 md/txt 很常见）时的降噪。

    真实案例：企业微信文档导出的 md 里整段 <font style="color:rgb(51,51,51)">，
    原样入库后检索召回的全是样式代码，污染检索还把面试官出题带偏。
    标签数量超过阈值才处理，避免误伤正文里少量 <code> 之类的记号。
    """
    if len(re.findall(r"<[a-zA-Z/][^>]*>", text)) < min_tags:
        return text
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|h[1-6]|section|font|span)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(entity, char)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _load_html(path: Path) -> str:
    """HTML 简历：剥标签取文本，块级标签转成换行以保住板块边界。"""
    return _strip_html_noise(path.read_text(encoding="utf-8", errors="ignore"), min_tags=0)
