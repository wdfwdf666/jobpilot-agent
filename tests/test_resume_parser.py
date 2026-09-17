"""简历解析器单元测试：板块识别、姓名抽取、降级分支、分块前缀。

跑：pytest tests/test_resume_parser.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE = """# 张明 · AI Agent 应用开发工程师

基本信息
- 电话：13800000000 ｜ 邮箱：zhangming@example.com

教育经历
- 2020.09 - 2023.06　某某大学　计算机技术　硕士

专业技能
- 编程语言：Python（主力）、TypeScript

项目经历
- JobPilot 智能求职助手 Agent
  - 设计 Supervisor 多 Agent 编排，支持真流式输出
"""


def test_sections_detected():
    from app.rag.resume_parser import parse_resume

    profile = parse_resume(SAMPLE)
    assert profile.structured is True
    assert profile.section_names == ["基本信息", "教育经历", "专业技能", "项目经历"]
    assert "Supervisor" in profile.section("项目经历")


def test_name_and_contact():
    from app.rag.resume_parser import parse_resume

    profile = parse_resume(SAMPLE)
    assert profile.name == "张明"
    assert profile.contact["email"] == "zhangming@example.com"
    assert profile.contact["phone"] == "13800000000"


def test_numbered_and_english_headings():
    from app.rag.resume_parser import parse_resume

    text = "一、教育经历\n本科\n\n二、项目经历\n项目A\n\n三、Skills\nPython, SQL\n\nProjects\n项目B\n"
    profile = parse_resume(text)
    assert "教育经历" in profile.section_names
    assert "项目经历" in profile.section_names
    assert "专业技能" in profile.section_names


def test_fallback_when_no_headings():
    """PDF 常见：抽取后没有标题层级 -> 降级为全文单板块，不能抛异常。"""
    from app.rag.resume_parser import parse_resume

    text = "Name: Zhang Ming\nPhone: 13800000000\nWorked on RAG pipeline for 3 years."
    profile = parse_resume(text)
    assert profile.structured is False
    assert profile.section_names == ["全文"]


def test_blocks_carry_section_prefix():
    from app.rag.resume_parser import parse_resume

    profile = parse_resume(SAMPLE)
    blocks = profile.to_blocks(chunk_size=200, overlap=30)
    assert blocks, "应至少产出 1 个块"
    assert all(b["text"].startswith(f"【{b['section']}】") for b in blocks)


def test_body_text_not_mistaken_for_heading():
    """正文里出现'项目'不应被当成标题（曾经的解析 bug）。"""
    from app.rag.resume_parser import parse_resume

    text = "项目经历\n- 负责某项目的整体架构设计与落地，历时一年\n- 项目上线后性能提升 3 倍\n"
    profile = parse_resume(text)
    assert profile.section_names == ["项目经历"]
    assert "性能提升 3 倍" in profile.section("项目经历")
