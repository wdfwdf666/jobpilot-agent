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
    """PDF 抽取：优先 PyMuPDF 按坐标恢复阅读顺序，失败降级 pypdf。

    pypdf 按 PDF 内部对象流输出，简历模板（文本框排版）的抽取顺序与视觉
    顺序完全脱节——标题出现在错误位置，板块切分整体错位（真实踩坑：
    项目经历被标成专业技能、基本信息被标成项目经历）。PyMuPDF 的 blocks
    自带坐标，按 y 再 x 排序即可恢复视觉阅读顺序。
    """
    try:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            parts: list[str] = []
            for page in doc:
                # words 模式：按 y 聚类成视觉行（容差 3pt）、行内按 x 排序。
                # 比 blocks 更稳——blocks 会把"姓 名"这类拉开字距的字段拆成两行
                words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
                rows: list[list] = []
                cur: list = []
                cur_y: float | None = None
                for w in words:
                    if cur_y is None or abs(w[1] - cur_y) <= 3:
                        cur.append(w)
                        cur_y = w[1] if cur_y is None else cur_y
                    else:
                        rows.append(cur)
                        cur, cur_y = [w], w[1]
                if cur:
                    rows.append(cur)
                parts.append("\n".join(
                    " ".join(x[4] for x in sorted(r, key=lambda w: w[0])) for r in rows
                ))
            # \uf0xx 是 PDF 字体映射出的 Wingdings 项目符号，入库前清掉
            text = "\n\n".join(parts)
            text = re.sub(r"[\uf000-\uf0ff]", " ", text)
            if text.strip():
                return text
    except ImportError:
        pass  # 未装 pymupdf 时退回 pypdf（顺序可能错乱，但聊胜于无）
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _load_docx(path: Path) -> str:
    """docx 抽取：正文段落 + 表格 + 文本框。

    简历模板几乎都把正文装在文本框（w:txbxContent）里，python-docx 的
    document.paragraphs 只看 body 顶层段落，251KB 的简历只能抽到页眉式
    的一行字（真实踩坑）。文本框在 XML 里常带 mc:AlternateContent 的
    Choice/Fallback 双份拷贝，必须按内容去重，否则全文重复一遍。
    """
    import docx

    document = docx.Document(str(path))
    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]

    # 表格（含嵌套表格）：findall 递归取所有段落，每个节点只取一次
    for table in document.tables:
        for p in table._tbl.findall(f".//{_W_NS}p"):
            text = "".join(t.text or "" for t in p.findall(f".//{_W_NS}t")).strip()
            if text:
                parts.append(text)

    # 文本框：Choice/Fallback 产生完全相同的两份，按整块文本保序去重
    for block in _textbox_blocks(document.element.body):
        parts.append(block)

    return "\n\n".join(parts)


def _textbox_blocks(body_el) -> list[str]:
    """按 XML 顺序抽取文本框文本（简历模板中文本框创建顺序≈视觉顺序）。"""
    blocks: list[str] = []
    for txbx in body_el.findall(f".//{_W_NS}txbxContent"):
        lines = []
        for p in txbx.findall(f".//{_W_NS}p"):
            text = "".join(t.text or "" for t in p.findall(f".//{_W_NS}t")).strip()
            if text:
                lines.append(text)
        block = "\n".join(lines)
        if block:
            blocks.append(block)
    return list(dict.fromkeys(blocks))  # Choice/Fallback 去重


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
