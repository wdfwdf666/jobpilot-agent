"""面试官 Agent：从个人知识库检索专业知识点出题，回答后对照要点评分追问。

状态机（这是"模拟面试"区别于普通聊天的关键）：
  出题轮（无待答问题）：检索知识库 -> 生成面试题 -> 题目挂起
  批改轮（有待答问题）：结构化批改（JSON）-> 流式口语化点评 -> 追问题成为新的挂起题目

多轮对话：维护 messages 历史；评分时把知识库标准答案要点作为评判依据。
已知限制（TODO）：历史挂在进程级单例上，多会话会串味；接多用户前要按 session 隔离。
"""
import json

from app.agents import llm
from app.rag.retriever import get_retriever
from app.schemas import RetrievedChunk, parse_json_output

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
        # 必须走进程级单例：并发请求线程里直接构造 Retriever 会重演 chroma 冷启动竞态
        self._retriever = get_retriever()
        self._history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._pending_question: str | None = None

    # --- 状态机入口 -------------------------------------------------------------

    def next_turn(
        self, user_message: str, emit, references: list[RetrievedChunk]
    ) -> tuple[str, dict]:
        """推进一轮面试，返回 (回复全文, 额外 artifacts)。

        无待答问题 -> 出题；有待答问题 -> 批改 + 追问。
        """
        if self._pending_question is None:
            return self._ask(user_message, emit, references), {}
        return self._grade_and_followup(user_message, emit, references)

    # --- 出题轮 -----------------------------------------------------------------

    def _ask(self, user_message: str, emit, references: list[RetrievedChunk]) -> str:
        emit({"status": "正在从知识库选题…"})
        # 主题直接取用户原话（"考我 RAG"->RAG），比从检索片段截取前缀稳定得多
        topic = user_message.strip()[:60] or "Python 后端开发"
        chunks = references or self.retrieve_reference(topic, category="八股文")
        reference_text = "\n".join(f"- {c.text[:200]}" for c in chunks[:4]) or "（知识库暂无相关内容，可自由出题）"
        question = llm.chat([{
            "role": "user",
            "content": f"用户想练习面试（原话：「{topic}」）。请围绕其中的技术主题出一道面试题，"
                       f"只输出问题本身，不要客套、不要点评。\n"
                       f"可参考的知识点：\n{reference_text}",
        }])
        self._history.append({"role": "assistant", "content": question})
        self._pending_question = question
        # 出题是短文本，分块推送保持与其它 Agent 一致的渐进渲染
        for i in range(0, len(question), 32):
            emit({"delta": question[i:i + 32]})
        return question

    # --- 批改轮 -----------------------------------------------------------------

    def _grade_and_followup(
        self, user_message: str, emit, references: list[RetrievedChunk]
    ) -> tuple[str, dict]:
        emit({"status": "正在对照知识库批改…"})
        question = self._pending_question or ""
        try:
            grade = self.grade_answer(question, user_message, references)
        except Exception:  # noqa: BLE001 批改失败不应中断面试，退化为普通点评
            grade = None

        extra: dict = {}
        grade_context = ""
        if grade:
            extra["grade"] = grade
            followup = str(grade.get("followup") or "").strip()
            grade_context = (
                f"候选人刚回答了你的问题「{question}」。\n"
                f"系统对照知识库的批改结果（JSON）：{json.dumps(grade, ensure_ascii=False)}\n"
                "请据此用 2-3 句口语点评（先说对了什么，再说缺了什么），"
                + (f"最后自然地引出追问：「{followup}」。" if followup else "然后出下一道相关题目。")
                + "\n不要输出 JSON，不要复述批改结果原文。"
            )
            # 追问题成为下一轮的挂起题目；没有追问则下轮重新出题
            self._pending_question = followup or None

        reply = self.chat_stream(user_message, emit, references=references,
                                 extra_system=grade_context or None)
        return reply, extra

    # --- 基础能力 ---------------------------------------------------------------

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
        return parse_json_output(raw)

    def chat_stream(
        self,
        user_message: str,
        emit,
        references: list[RetrievedChunk] | None = None,
        extra_system: str | None = None,
    ) -> str:
        """流式聊天：每个增量回调 emit({"delta": ...})，结束后落历史并返回全文。

        references 为知识库检索到的标准要点（不写入对话历史，只作为本轮上下文），
        让点评和追问有依据，而不是模型自由发挥；extra_system 用于注入批改结果等
        单轮上下文。
        """
        self._history.append({"role": "user", "content": user_message})
        messages = list(self._history)
        insert_at = 1
        if references:
            reference_text = "\n---\n".join(r.text for r in references)
            messages.insert(insert_at, {
                "role": "system",
                "content": f"以下是知识库中的相关标准要点，点评候选人回答时请对照它们，不要编造：\n{reference_text}",
            })
            insert_at += 1
        if extra_system:
            messages.insert(insert_at, {"role": "system", "content": extra_system})
        parts: list[str] = []
        for delta in llm.chat_stream(messages):
            parts.append(delta)
            emit({"delta": delta})
        reply = "".join(parts)
        self._history.append({"role": "assistant", "content": reply})
        return reply

    @property
    def pending_question(self) -> str | None:
        return self._pending_question

    def end_interview(self) -> None:
        """结束当前面试：清掉挂起问题与对话历史，让路由恢复正常。"""
        self._pending_question = None
        self._history = [{"role": "system", "content": SYSTEM_PROMPT}]
