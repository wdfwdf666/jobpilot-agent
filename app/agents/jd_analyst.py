"""JD 分析 Agent：抽取结构化要求 -> 结合简历与知识库做匹配打分。

输出强制走 JDAnalysis/MatchResult 契约（结构化输出），杜绝自由发挥。
"""
from app.agents import llm
from app.schemas import JDAnalysis, MatchResult, parse_json_output

ANALYZE_PROMPT = """你是资深技术招聘专家。分析以下岗位 JD，抽取结构化信息。
只输出 JSON，字段：position, hard_requirements, soft_requirements, implicit_preferences, keywords。

岗位 JD：
{jd_text}"""


def analyze_jd(jd_text: str) -> JDAnalysis:
    raw = llm.chat(
        [{"role": "user", "content": ANALYZE_PROMPT.format(jd_text=jd_text)}],
        json_mode=True,
    )
    return JDAnalysis(**parse_json_output(raw))


MATCH_PROMPT = """你是简历匹配专家。对照岗位要求逐条匹配候选人简历，给出评分和改进建议。
规则（防幻觉）：evidence 必须是简历原文片段，简历中找不到就置空并在 gap_advice 给出补强建议。
只输出 JSON：overall_score, items[{requirement, evidence, score, gap_advice}]。

岗位要求：{requirements}

候选人简历相关内容：
{resume_context}"""


def match_resume(jd_analysis: JDAnalysis, resume_context: str) -> MatchResult:
    raw = llm.chat(
        [{
            "role": "user",
            "content": MATCH_PROMPT.format(
                requirements="\n".join(jd_analysis.hard_requirements + jd_analysis.soft_requirements),
                resume_context=resume_context[:6000],
            ),
        }],
        json_mode=True,
    )
    return MatchResult(**parse_json_output(raw))
