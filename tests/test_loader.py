"""loader 的回归测试：HTML 降噪 / docx 文本框抽取 / PDF 坐标排序。"""
import pytest

from app.rag.loader import _load_pdf, _strip_html_noise, _textbox_blocks


def test_heavy_html_gets_stripped():
    dirty = "\n".join(
        f'<span style="color:rgb(51, 51, 51)">第 {i} 行正文内容</span>' for i in range(30)
    )
    clean = _strip_html_noise(dirty)
    assert "<span" not in clean
    assert "第 0 行正文内容" in clean


def test_light_markdown_untouched():
    md = "# 标题\n\n正文里有 `code` 和 <br> 少量标签，不应被处理。\n" * 3
    assert _strip_html_noise(md) == md  # 标签数低于阈值，原样返回


def test_script_style_removed():
    dirty = "<style>p{color:red}</style>" + "<p>正文</p>" * 30
    clean = _strip_html_noise(dirty)
    assert "<style" not in clean
    assert "正文" in clean


# ---- docx 文本框（真实踩坑：简历模板正文全在 txbxContent 里，paragraphs 抽不到） ----

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _txbx_xml(inner: str) -> str:
    """构造带 Choice/Fallback 双份拷贝的文本框 XML（Word 实际输出形态）。"""
    return (
        f'<mc:AlternateContent xmlns:mc="{_MC}" xmlns:w="{_W}">'
        f"<mc:Choice><w:txbxContent>{inner}</w:txbxContent></mc:Choice>"
        f"<mc:Fallback><w:txbxContent>{inner}</w:txbxContent></mc:Fallback>"
        f"</mc:AlternateContent>"
    )


def _p(text: str) -> str:
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def test_textbox_blocks_dedup_choice_fallback():
    from docx.oxml import parse_xml

    body = parse_xml(
        f'<w:body xmlns:w="{_W}" xmlns:mc="{_MC}">'
        + _txbx_xml(_p("姓名：测试") + _p("教育背景"))
        + _txbx_xml(_p("姓名：测试") + _p("教育背景"))  # Fallback 重复
        + _txbx_xml(_p("项目经历"))
        + "</w:body>"
    )
    blocks = _textbox_blocks(body)
    assert len(blocks) == 2  # 3 个 txbxContent -> 2 个唯一块
    assert blocks[0].split("\n") == ["姓名：测试", "教育背景"]
    assert blocks[1] == "项目经历"


def test_textbox_blocks_skip_empty():
    from docx.oxml import parse_xml

    body = parse_xml(
        f'<w:body xmlns:w="{_W}" xmlns:mc="{_MC}">'
        + _txbx_xml("<w:p/>")  # 空文本框（真实简历里存在）
        + _txbx_xml(_p("技能"))
        + "</w:body>"
    )
    assert _textbox_blocks(body) == ["技能"]


# ---- PDF 坐标排序（pypdf 对象流顺序会把简历板块错位） ----

try:
    import pymupdf  # noqa: F401

    _HAS_PYMUPDF = True
except ImportError:
    _HAS_PYMUPDF = False


@pytest.mark.skipif(not _HAS_PYMUPDF, reason="pymupdf 未安装")
def test_pdf_load_respects_visual_order():
    """注意不用 tmp_path：部分 Windows 沙盒环境禁止 pytest 在系统 temp 建目录。"""
    import os
    from pathlib import Path

    pdf_path = Path(__file__).parent / "_loader_test.pdf"
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 700), "PROJECTS HEADER")  # y 大 = 页面下方，先写入
    page.insert_text((72, 100), "SKILLS LINE")  # y 小 = 页面上方，后写入
    doc.save(str(pdf_path))
    doc.close()

    try:
        text = _load_pdf(pdf_path)
        skills_pos = text.index("SKILLS LINE")
        projects_pos = text.index("PROJECTS HEADER")
        assert skills_pos < projects_pos  # 视觉在上面的行必须排在前面
    finally:
        os.remove(pdf_path)
