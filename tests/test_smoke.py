"""冒烟测试：骨架能 import、能建图。跑：pytest tests/ -v"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_import_app():
    import app.main  # noqa: F401


def test_planner_rules():
    from app.agents.planner import route

    assert route("帮我分析这个JD") == "jd_analysis"
    assert route("优化一下我的简历") == "resume_advice"
    assert route("开始模拟面试") == "mock_interview"
    assert route("今天天气怎么样") == "general_chat"


def test_chunker():
    from app.rag.chunker import chunk_text

    text = "# 标题一\n" + "内容。" * 100 + "\n\n# 标题二\n" + "知识点。" * 80
    chunks = chunk_text(text, chunk_size=200, overlap=30)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
