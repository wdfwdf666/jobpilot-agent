"""共享类型定义：Agent 之间通过结构化数据传递，而不是自由文本。"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class JDAnalysis(BaseModel):
    """JD 结构化抽取结果（JD 分析 Agent 的输出契约）。"""
    position: str = Field(description="岗位名称")
    hard_requirements: list[str] = Field(description="硬性要求：学历/年限/必备技能")
    soft_requirements: list[str] = Field(description="软性要求与加分项")
    implicit_preferences: list[str] = Field(description="从 JD 措辞推断的隐性偏好")
    keywords: list[str] = Field(description="检索用关键词")


class MatchItem(BaseModel):
    requirement: str
    evidence: str = Field(description="简历原文证据片段，找不到则为空")
    score: int = Field(ge=0, le=100, description="匹配度 0-100")
    gap_advice: str = ""


class MatchResult(BaseModel):
    overall_score: int
    items: list[MatchItem]


class RetrievedChunk(BaseModel):
    text: str
    source: str = ""
    category: str = ""
    score: float = 0.0


def parse_json_output(raw: str) -> dict[str, Any]:
    """容错解析 LLM 的 JSON 输出（剥掉 markdown 代码块围栏）。

    解析失败时带上原始输出前 200 字符再抛错——只报 "Expecting value" 根本没法排查。
    """
    import json
    import re

    text = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 输出不是合法 JSON（{e}），原始输出前 200 字符：{raw[:200]!r}") from e
