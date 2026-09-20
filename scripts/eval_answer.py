"""LLM-as-judge 回答质量评测（D11）。

流程：每个问题 -> 混合检索 top3 作为上下文 -> LLM 生成回答 ->
再用 LLM 当裁判，按两个维度 1-5 打分（JSON 结构化输出）：
- faithfulness 忠实度：回答是否只基于检索片段，没有编造（衡量 RAG 是否压住幻觉）
- relevance 相关性：回答是否切题、覆盖了问题要点

面试话术：回答质量不能靠"感觉变好了"，裁判模型 + 结构化评分让每次
改动（换模型 / 调分块 / 换检索模式）都有可回归对比的数字。
用法：python scripts/eval_answer.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.llm import chat  # noqa: E402
from app.rag.retriever import get_retriever  # noqa: E402

QUESTIONS = [
    "GIL 会影响多核性能吗？为什么？",
    "CNN 里感受野是什么，怎么增大？",
    "few-shot 提示词应该怎么写才有效？",
    "候选人有没有做过 RAG 相关的项目？",
    "池化层的作用是什么？",
]

JUDGE_PROMPT = """你是严格的评测裁判。对照【检索依据】评判【回答】的质量，只输出 JSON：
{{"faithfulness": 1-5, "relevance": 1-5, "reason": "一句话理由"}}
- faithfulness（忠实度）：回答是否只依据检索依据的内容，出现依据里没有的事实则扣分
- relevance（相关性）：回答是否切题并覆盖问题要点

【问题】{question}

【检索依据】
{context}

【回答】{answer}"""


def build_answer(question: str) -> tuple[str, list[dict]]:
    """检索增强生成：top3 片段拼进 prompt（与线上 Agent 相同的链路）。"""
    hits = get_retriever().search(question, top_k=3)
    context = "\n\n".join(f"[片段{i+1}] {h['text'][:400]}" for i, h in enumerate(hits))
    answer = chat([
        {"role": "system", "content": "你是求职助手，只依据给定的检索依据回答问题，"
                                      "依据不足就明确说不知道，不要编造。"},
        {"role": "user", "content": f"检索依据：\n{context}\n\n问题：{question}"},
    ])
    return answer, hits


def judge(question: str, answer: str, hits: list[dict]) -> dict:
    context = "\n".join(f"- {h['text'][:200]}" for h in hits)
    raw = chat([{"role": "user", "content": JUDGE_PROMPT.format(
        question=question, context=context, answer=answer)}],
        json_mode=True, temperature=0)
    try:
        text = raw.strip()
        if "```" in text:  # 剥掉 markdown 围栏
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return {"faithfulness": 0, "relevance": 0, "reason": f"裁判输出解析失败: {raw[:100]}"}


def main() -> None:
    scores = []
    for q in QUESTIONS:
        answer, hits = build_answer(q)
        s = judge(q, answer, hits)
        scores.append(s)
        print(f"\nQ: {q}")
        print(f"  忠实度 {s.get('faithfulness')}/5  相关性 {s.get('relevance')}/5  -- {s.get('reason', '')}")
    n = len(scores)
    print("\n========== 汇总 ==========")
    print(f"平均忠实度: {sum(s.get('faithfulness', 0) for s in scores) / n:.2f}/5")
    print(f"平均相关性: {sum(s.get('relevance', 0) for s in scores) / n:.2f}/5")


if __name__ == "__main__":
    main()
