"""面试官 Agent：从个人知识库检索专业知识点出题，回答后对照要点评分追问。

状态机（这是"模拟面试"区别于普通聊天的关键）：
  出题轮（无待答问题）：检索知识库 -> 生成面试题 -> 题目挂起
  批改轮（有待答问题）：结构化批改（JSON）-> 流式口语化点评 -> 追问题成为新的挂起题目

多轮对话：维护 messages 历史；评分时把知识库标准答案要点作为评判依据。
会话隔离：本对象由 graph.get_interviewer(session_id) 按会话持有，挂起题目与
评分记录都不会跨会话泄漏（此前是全局单例，真实踩坑：新会话被旧题目接管）。

除了实时点评，本 Agent 还负责"兜住结果"：每一轮批改都留档（题目/回答/得分/
缺失要点/参考来源），面试结束后由 build_report() 聚合成一份 Markdown 报告，
并针对薄弱点回链到知识库里该补的板块。面试官不应该只给一句点评就结束。
"""
import json
from datetime import datetime

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

# 报告里回链知识库时用的默认板块：出题与评分都以"八股文"为标准要点来源
DEFAULT_REFERENCE_CATEGORY = "八股文"


class InterviewerAgent:
    def __init__(self) -> None:
        # 必须走进程级单例：并发请求线程里直接构造 Retriever 会重演 chroma 冷启动竞态
        self._retriever = get_retriever()
        self._history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._pending_question: str | None = None
        # 逐题评分留档：只在本场面试内累积，结束后仍保留，直到开新一场
        self._records: list[dict] = []
        self._ended = False

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
        # 上一场已结束、现在又出题 = 开了新的一场：清掉旧记录，
        # 否则新面试的报告里会混进上一场的分数。
        if self._ended:
            self._records = []
            self._ended = False
        emit({"status": "正在从知识库选题…"})
        # 主题直接取用户原话（"考我 RAG"->RAG），比从检索片段截取前缀稳定得多
        topic = user_message.strip()[:60] or "Python 后端开发"
        chunks = references or self.retrieve_reference(topic, category=DEFAULT_REFERENCE_CATEGORY)
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
            self._record(question, user_message, grade, references)
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

    def _record(
        self,
        question: str,
        answer: str,
        grade: dict,
        references: list[RetrievedChunk],
    ) -> None:
        """留档一轮评分：题目、回答、得分、对/错要点、参考来源。

        只留字段不留模型原始输出——报告是给人看的，也是给前端渲染的契约。
        """
        def _ints(value: object) -> int:
            try:
                return int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return 0

        self._records.append({
            "question": question,
            "answer": answer,
            "score": max(0, min(100, _ints(grade.get("score")))),
            "correct_points": [str(p) for p in (grade.get("correct_points") or [])],
            "missing_points": [str(p) for p in (grade.get("missing_points") or [])],
            "followup": str(grade.get("followup") or ""),
            "reference_sources": sorted({c.source for c in references if c.source}),
        })

    # --- 基础能力 ---------------------------------------------------------------

    def retrieve_reference(self, query: str, category: str = DEFAULT_REFERENCE_CATEGORY,
                           top_k: int = 4) -> list[dict]:
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

    # --- 评分报告 ---------------------------------------------------------------

    @property
    def pending_question(self) -> str | None:
        return self._pending_question

    @property
    def records(self) -> list[dict]:
        return list(self._records)

    def build_report(self, session_id: str = "") -> dict:
        """把本场面试的评分记录聚合成 Markdown 报告 + 结构化的知识库回链。

        "回链"具体指两件事：
        1. 每个薄弱知识点给出可检索关键词与所属知识库板块（category）；
        2. 直接在该板块里检出最相关的原文块（来源文件名），
           让"该补哪一块"落到具体文档上，而不是一句空泛的"多看看 RAG"。
        """
        records = self._records
        if not records:
            return {
                "session_id": session_id,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "turns": 0,
                "average_score": 0,
                "markdown": "# 模拟面试评分报告\n\n本场还没有产生评分记录。"
                            "先结束一轮问答（先出题、再作答）再导出。\n",
                "knowledge_gaps": [],
            }

        scores = [r["score"] for r in records]
        average = round(sum(scores) / len(scores))
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 逐题表格 + 薄弱点回链
        table = ["| # | 面试题 | 得分 | 主要缺失 |", "|---|---|---|---|"]
        for i, rec in enumerate(records, 1):
            missing = "；".join(rec["missing_points"][:3]) or "—"
            question = rec["question"].replace("\n", " ").strip()
            table.append(f"| {i} | {_shorten(question, 40)} | {rec['score']} | {_shorten(missing, 60)} |")

        gaps = self._knowledge_gaps(records)
        gap_sections: list[str] = []
        for i, gap in enumerate(gaps, 1):
            lines = [f"### {i}. {_shorten(gap['question'], 60)}", ""]
            lines.append(f"- 缺失要点：{'；'.join(gap['missing_points']) or '—'}")
            lines.append(f"- 建议复习板块：知识库「{gap['category']}」")
            lines.append(f"- 可检索关键词：`{gap['query']}`")
            if gap["hits"]:
                lines.append("- 库里已有的相关材料：")
                for hit in gap["hits"]:
                    lines.append(
                        f"  - 《{hit['source'] or '未命名'}》"
                        f"（{hit['category'] or '未分类'}）：{_shorten(hit['text'], 80)}"
                    )
            else:
                lines.append("- 库里暂时没有匹配材料，建议先往「八股文」板块补充这个知识点")
            gap_sections.append("\n".join(lines))

        markdown = "\n".join([
            "# 模拟面试评分报告",
            "",
            f"- 生成时间：{generated_at}",
            f"- 本场题数：{len(records)}",
            f"- 平均得分：**{average}/100**",
            "",
            "## 逐题评分",
            "",
            *table,
            "",
            "## 待补强的知识点（回链知识库）",
            "",
            *(gap_sections or ["本场没有明显缺失要点 👍"]),
            "",
            "## 逐题明细",
            "",
            *[_detail_block(i, rec) for i, rec in enumerate(records, 1)],
        ])

        return {
            "session_id": session_id,
            "generated_at": generated_at,
            "turns": len(records),
            "average_score": average,
            "markdown": markdown,
            "knowledge_gaps": gaps,
        }

    def _knowledge_gaps(self, records: list[dict]) -> list[dict]:
        """从缺失要点生成"该补的知识板块"，并检出该板块里最相关的原文块。"""
        gaps: list[dict] = []
        for rec in records:
            missing = rec["missing_points"]
            if not missing:
                continue
            # query 取缺失要点本身（模型输出的短句），比整道题更贴近"该补什么"；
            # label 用题目短句，因为 query 常常是一整句评判语，直接当按钮文案会撑爆 UI。
            # 检索与前端 chip 搜索都用同一个 query——用户点到的就是产出这些材料的那次检索。
            query = _shorten(" ".join(missing), 40) or _shorten(rec["question"], 40)
            hits = self.retrieve_reference(query, category=DEFAULT_REFERENCE_CATEGORY, top_k=3)
            gaps.append({
                "question": rec["question"],
                "label": _shorten(rec["question"], 26),
                "missing_points": missing,
                "category": DEFAULT_REFERENCE_CATEGORY,
                "query": query,
                "hits": [
                    {
                        "source": h["metadata"].get("source", ""),
                        "category": h["metadata"].get("category", ""),
                        "text": _shorten(h["text"], 200),
                    }
                    for h in hits
                ],
            })
        return gaps

    def end_interview(self) -> None:
        """结束当前面试：清掉挂起问题与对话历史，让路由恢复正常。

        刻意**保留** `_records`：报告是在"结束之后"导出的，
        清掉就再也拼不出来了。开新一场时（_ask）才会重置。
        """
        self._pending_question = None
        self._history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._ended = True


def _shorten(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _detail_block(index: int, rec: dict) -> str:
    lines = [f"### {index}. {_shorten(rec['question'], 60)}", ""]
    lines.append(f"**得分：{rec['score']}/100**")
    lines.append("")
    if rec["correct_points"]:
        lines.append(f"- 答对：{'；'.join(rec['correct_points'])}")
    if rec["missing_points"]:
        lines.append(f"- 缺失：{'；'.join(rec['missing_points'])}")
    if rec["followup"]:
        lines.append(f"- 追问：{rec['followup']}")
    if rec["reference_sources"]:
        lines.append(f"- 评分依据来源：{'、'.join(rec['reference_sources'])}")
    lines.append("")
    return "\n".join(lines)
