"""Planner：意图识别与路由。V1 用规则，P1 阶段升级为 LLM 意图分类。

面试考点：为什么不全用 LLM 路由？-> 高频简单意图走规则（零延迟零成本），
模糊意图才升级 LLM 分类，是成本/效果的标准取舍。
"""
from typing import Literal

Intent = Literal["jd_analysis", "resume_advice", "mock_interview", "general_chat"]

RULES: list[tuple[Intent, tuple[str, ...]]] = [
    ("jd_analysis", ("jd", "岗位", "职位描述", "匹配", "match")),
    ("resume_advice", ("简历", "优化", "改一改", "建议", "resume")),
    ("mock_interview", ("面试", "考我", "提问", "interview", "模拟")),
]


def route(user_message: str) -> Intent:
    text = user_message.lower()
    for intent, keywords in RULES:
        if any(kw in text for kw in keywords):
            return intent
    return "general_chat"

# TODO(P1): 规则未命中时调用 LLM 做意图分类，枚举与上面对齐
