"""LLM-as-judge 回答质量评测（D11）。

流程：每个问题 -> 混合检索 top3 作为上下文 -> LLM 生成回答 ->
再用 LLM 当裁判，按两个维度 1-5 打分（JSON 结构化输出）：
- faithfulness 忠实度：回答是否只基于检索片段，没有编造（衡量 RAG 是否压住幻觉）
- relevance 相关性：回答是否切题、覆盖了问题要点

用法：
    python scripts/eval_answer.py                  # 汇总分数 + 低分告警
    python scripts/eval_answer.py -v               # 同时打印检索依据与完整回答（定位低分原因）
    python scripts/eval_answer.py --save base.json # 存档基线
    python scripts/eval_answer.py --baseline base.json   # 改动后对比涨跌

怎么用这两个分数定位问题：
- 忠实度高 + 相关性低  -> 检索没给对料，回答只能顾左右而言他 -> 调检索/分块
- 忠实度低             -> 检索片段不够或模型硬编 -> 补知识 / 收紧 system prompt
- 两个都高但读着不对   -> 评测集或裁判 prompt 有问题 -> 先修尺子

面试话术：回答质量不能靠"感觉变好了"，裁判模型 + 结构化评分让每次
改动（换模型 / 调分块 / 换检索模式）都有可回归对比的数字。
"""
import argparse
import json
import sys
from datetime import datetime
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

LOW_SCORE = 3  # 低于等于这个分数视为需要复盘的用例

JUDGE_PROMPT = """你是严格的评测裁判。对照【检索依据】评判【回答】的质量，只输出 JSON：
{{"faithfulness": 1-5, "relevance": 1-5, "reason": "一句话理由"}}
- faithfulness（忠实度）：回答是否只依据检索依据的内容，出现依据里没有的事实则扣分
- relevance（相关性）：回答是否切题并覆盖了问题要点

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


def summarize(results: list[dict]) -> dict:
    n = len(results) or 1
    return {
        "faithfulness": sum(r.get("faithfulness", 0) for r in results) / n,
        "relevance": sum(r.get("relevance", 0) for r in results) / n,
    }


def print_diff(baseline: dict, current: dict) -> None:
    print("\n========== 与基线对比 ==========")
    b, c = baseline.get("summary", {}), current["summary"]
    for m in ("faithfulness", "relevance"):
        if m in b:
            print(f"  {m:<14} {b[m]:.2f} -> {c[m]:.2f}  ({c[m] - b[m]:+.2f})")
    b_cases = {x["question"]: x for x in baseline.get("cases", [])}
    for case in current.get("cases", []):
        old = b_cases.get(case["question"])
        if old and old.get("faithfulness") != case.get("faithfulness"):
            print(f"  忠实度变动：「{case['question']}」"
                  f"{old['faithfulness']} -> {case['faithfulness']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-as-judge 回答质量评测")
    parser.add_argument("-v", "--detail", action="store_true",
                        help="打印检索依据与完整回答（定位低分原因）")
    parser.add_argument("--save", metavar="PATH", help="把本次结果存档为 JSON")
    parser.add_argument("--baseline", metavar="PATH", help="与存档的基线 JSON 对比")
    args = parser.parse_args()

    records = []
    for q in QUESTIONS:
        answer, hits = build_answer(q)
        s = judge(q, answer, hits)
        records.append({
            "question": q,
            "answer": answer,
            "context": [h["text"][:200] for h in hits],
            "faithfulness": s.get("faithfulness"),
            "relevance": s.get("relevance"),
            "reason": s.get("reason", ""),
        })
        print(f"\nQ: {q}")
        low_f = (s.get("faithfulness") or 0) <= LOW_SCORE
        low_r = (s.get("relevance") or 0) <= LOW_SCORE
        print(f"  忠实度 {s.get('faithfulness')}/5  相关性 {s.get('relevance')}/5"
              f"{'   ⚠ 需复盘' if low_f or low_r else ''}")
        print(f"  -- {s.get('reason', '')}")
        if args.detail:
            print("  [检索依据 top3]")
            for i, h in enumerate(hits, 1):
                src = h.get("metadata", {}).get("source", "?")
                print(f"    {i}. {src} | {' '.join(h['text'].split())[:90]}")
            print("  [回答]")
            for line in answer.splitlines():
                print(f"    {line}")
            if low_f:
                print("  ⚠ 忠实度低：核对回答里是否有检索依据之外的事实（典型=模型自带知识补全）")
            if low_r:
                print("  ⚠ 相关性低：核对检索 top3 是否真的覆盖了问题要点")

    summary = summarize(records)
    n = len(records)
    print("\n========== 汇总 ==========")
    print(f"平均忠实度: {summary['faithfulness']:.2f}/5")
    print(f"平均相关性: {summary['relevance']:.2f}/5")
    weak = [r["question"] for r in records
            if min(r.get("faithfulness") or 0, r.get("relevance") or 0) <= LOW_SCORE]
    print("需复盘用例：" + ("、".join(weak) if weak else "无 ✓"))

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "case_count": n,
        "cases": records,
    }
    if args.save:
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"已存档：{args.save}")
    if args.baseline:
        print_diff(json.loads(Path(args.baseline).read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    main()
