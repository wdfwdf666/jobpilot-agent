"""面试官 Agent：从个人知识库检索专业知识点出题，回答后对照要点评分追问。

多轮对话：维护 messages 历史；评分时把知识库标准答案要点作为评判依据。
"""
from app.agents import llm
from app.rag.retriever import Retriever
from app.schemas import ChatMessage, RetrievedChunk

SYSTEM_PROMPT = """你是严格但友善的技术面试官。根据目标岗位和候选人简历提问。
规则：
1. 优先围绕知识库中的专业知识点出题，一次只问一个问题；
2. 候选人回答后：先给 1-2 句点评（对照要点指出对错），再决定追问还是换题；
3. 语气专业，不客套。"""

GRADE_PROMPT = """对照标准要点评估候选人回答，输出 JSON：
{{"score": 0-100, "correct_points": [...], "missing_points": [...], "followup": "追问问题"}}

面试题：{question}
标准要点：{reference}
候选人回答：{answer}"""


class InterviewerAgent:
    def __init__(self) -> None:
        self._retriever = Retriever()
        self._history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def ask_question(self, jd_skills: list[str]) -> str:
        """按 JD 技能词检索知识库出题。"""
        topic = "、".join(jd_skills[:5]) if jd_skills else "后端开发"
        chunks = self.retrieve_reference(topic, category="八股文")
        question = llm.chat(self._history + [{
            "role": "user",
            "content": f"围绕这些主题出一道面试题（只输出问题本身）：{topic}"
                       + (f"\n可参考的知识点：\n" + "\n".join(c.text[:300] for c in chunks) if chunks else ""),
        }])
        self._history.append({"role": "assistant", "content": question})
        return question

    def retrieve_reference(self, query: str, category: str = "八股文", top_k: int = 4) -> list[dict]:
        """检索知识库里与该话题相关的知识点，作为出题/评分的标准要点依据。

        类别为空时降级为全库检索：知识库还没灌八股文时功能仍可用。
        """
        try:
            hits = self._retriever.search(query, top_k=top_k, category=category)
            if not hits:
                hits = self._retriever.search(query, top_k=top_k)
            return hits
        except Exception:  # noqa: BLE001 知识库不可用时面试官应继续工作，不阻塞对话
            return []

    def grade_answer(self, question: str, answer: str, reference_chunks: list[RetrievedChunk]) -> dict:
        raw = llm.chat([{
            "role": "user",
            "content": GRADE_PROMPT.format(
                question=question,
                reference="\n".join(c.text[:400] for c in reference_chunks) or "（无标准要点）",
                answer=answer,
            ),
        }], json_mode=True)
        from app.schemas import parse_json_output
        return parse_json_output(raw)

    def chat(self, user_message: str) -> str:
        self._history.append({"role": "user", "content": user_message})
        reply = llm.chat(self._history)
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def chat_stream(self, user_message: str, emit, references: list[RetrievedChunk] | None = None) -> str:
        """流式聊天：每个增量回调 emit({"delta": ...})，结束后落历史并返回全文。

        references 为知识库检索到的标准要点（不写入对话历史，只作为本轮上下文），
        让点评和追问有依据，而不是模型自由发挥。
        """
        self._history.append({"role": "user", "content": user_message})
        messages = list(self._history)
        if references:
            reference_text = "\n---\n".join(r.text for r in references)
            messages.insert(1, {
                "role": "system",
                "content": f"以下是知识库中的相关标准要点，点评候选人回答时请对照它们，不要编造：\n{reference_text}",
            })
        parts: list[str] = []
        for delta in llm.chat_stream(messages):
            parts.append(delta)
            emit({"delta": delta})
        reply = "".join(parts)
        self._history.append({"role": "assistant", "content": reply})
        return reply

    @property
    def history(self) -> list[dict[str, str]]:
        return self._history
