"""简历顾问 Agent：基于检索到的简历片段，生成"必须挂原文证据"的优化建议。

防幻觉机制：prompt 强制每条建议引用简历原文，检索不到证据的话题直接拒答。
"""
from app.agents import llm
from app.schemas import RetrievedChunk

ADVISOR_PROMPT = """你是简历优化顾问。基于候选人的简历片段回答问题或给出优化建议。

硬性规则：
1. 每条建议必须标注依据的简历原文（用「」括起来）；
2. 简历片段中没有依据的内容，必须明确说"简历中未找到相关信息"；
3. 建议要具体到"改哪一条、怎么改、为什么"，不要空泛。

简历相关片段：
{context}

用户问题：{question}"""


def advise(question: str, resume_chunks: list[RetrievedChunk]) -> str:
    context = "\n---\n".join(c.text for c in resume_chunks) or "（简历知识库为空）"
    return llm.chat([{"role": "user", "content": ADVISOR_PROMPT.format(context=context, question=question)}])
