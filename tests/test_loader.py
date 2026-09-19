"""loader 的 HTML 降噪回归测试：网页导出的 md/txt 不能把样式标签灌进知识库。"""
from app.rag.loader import _strip_html_noise


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
