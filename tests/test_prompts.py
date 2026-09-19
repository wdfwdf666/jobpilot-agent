"""Prompt 模板与结构化输出解析的回归测试。

背景（真实事故）：MATCH_PROMPT 里写了字面量 {requirement, evidence, ...}，
str.format() 把它当占位符解析，运行时抛 KeyError，
前端只显示一段引号包裹的字段名，极难定位。
"""
import pytest

from app.agents.interviewer import GRADE_PROMPT
from app.agents.jd_analyst import ANALYZE_PROMPT, MATCH_PROMPT
from app.schemas import parse_json_output


def test_analyze_prompt_format_ok():
    out = ANALYZE_PROMPT.format(jd_text="负责 Agent 平台研发")
    assert "Agent 平台研发" in out
    assert "position" in out  # 字段说明保留给模型看


def test_match_prompt_format_ok():
    """花括号必须被转义：format 后 JSON 字段列表应原样保留。"""
    out = MATCH_PROMPT.format(requirements="Python；RAG", resume_context="三年后端经验")
    assert "Python；RAG" in out
    assert "{requirement, evidence, score, gap_advice}" in out


def test_grade_prompt_format_ok():
    out = GRADE_PROMPT.format(question="GIL 是什么", reference="全局解释器锁", answer="不知道")
    assert "GIL 是什么" in out
    assert '"score"' in out


def test_parse_json_output_plain():
    assert parse_json_output('{"a": 1}') == {"a": 1}


def test_parse_json_output_fenced():
    raw = "```json\n{\"overall_score\": 80, \"items\": []}\n```"
    assert parse_json_output(raw) == {"overall_score": 80, "items": []}


def test_parse_json_output_invalid_has_context():
    with pytest.raises(ValueError, match="不是合法 JSON"):
        parse_json_output("这不是 JSON")
