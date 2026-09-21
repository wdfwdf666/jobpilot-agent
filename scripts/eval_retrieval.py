"""检索评测（D10-11）：纯向量 vs 混合检索的量化对比 + 逐条诊断。

评测集：每条 = 一个真实用户问题 + 期望命中的文档 + 必须出现的内容关键词。
指标：
- recall@k：top-k 里有没有命中期望文档
- MRR：命中的最靠前名次（1/rank 的均值），衡量"好结果排得够不够前"
- nDCG 类排序质量留给规模化后（当前库小，MRR 已够用）

用法：
    python scripts/eval_retrieval.py                     # 汇总对比表
    python scripts/eval_retrieval.py --check-corpus      # 【先跑这个】用例是否还和库对得上
    python scripts/eval_retrieval.py -v                  # 逐条诊断：哪条挂了、为什么、实际召回什么
    python scripts/eval_retrieval.py -v --mode hybrid     # 只看某一模式
    python scripts/eval_retrieval.py --save base.json     # 存基线
    python scripts/eval_retrieval.py --save new.json --baseline base.json   # 改动后对比涨跌
    RRF_K=10 python scripts/eval_retrieval.py             # 换个参数重跑（网格搜索）

命中判定（strict，默认）：
    期望来源匹配 AND 关键词真的出现在该块文本里（拉丁字母按词边界匹配）。
为什么不用"来源匹配就算命中"（loose，--loose 可复现旧口径）：
    实测踩坑——关键词 "rag" 用子串匹配会命中 "Average Pooling"，
    来源匹配则会让"文档被召回但内容不相关"也算命中，
    两者叠加把 recall 抬到 100%，指标彻底失去分辨力（尺子坏了，量什么都是满分）。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.retriever import get_retriever  # noqa: E402
from app.rag.vectorstore import get_vector_store  # noqa: E402

K = 5  # 检索深度（recall@5 需要拿到 5 条，固定不开放，避免口径漂移）

# 评测集：query -> 期望来源（可选，子串匹配）/ 期望类别（可选）/ 必须出现的关键词
# 注意：用例是跟着"当前知识库"走的。换库/换文档后先跑 --check-corpus，
# 它会指出哪些用例已经失效（期望文档不在了、或关键词在全库都找不到）。
EVAL_SET = [
    # --- 八股文：专有名词/缩写类（BM25 的主场，向量容易翻车）---
    {"id": "py-gil", "query": "GIL 会影响多核性能吗",
     "source": "sample_python_notes", "keywords": ["GIL"]},
    {"id": "py-decorator", "query": "装饰器的本质是什么",
     "source": "sample_python_notes", "keywords": ["装饰器"]},
    {"id": "cnn-receptive", "query": "CNN 感受野怎么计算",
     "source": "92.卷积神经网络", "keywords": ["感受野"]},
    {"id": "cnn-padding", "query": "卷积神经网络 padding 的作用",
     "source": "92.卷积神经网络", "keywords": ["Padding"]},
    {"id": "cnn-pooling", "query": "池化层有什么用",
     "source": "92.卷积神经网络", "keywords": ["池化"]},
    {"id": "cnn-stride", "query": "步长会怎么影响特征图",
     "source": "92.卷积神经网络", "keywords": ["步长"]},
    # --- 提示词工程（语义改写类，向量的主场）---
    {"id": "pe-fewshot", "query": "few-shot 示例能解决什么问题",
     "source": "1.提示词工程", "keywords": ["Few-shot"]},
    {"id": "pe-cost", "query": "提示词工程能降低微调成本吗",
     "source": "1.提示词工程", "keywords": ["微调"]},
    {"id": "pe-why", "query": "为什么需要提示词工程",
     "source": "1.提示词工程", "keywords": ["提示词工程"]},
    # --- 简历素材：按类别定位（不硬编码文件名，仓库公开时不带个人信息）---
    {"id": "cv-project", "query": "候选人做过智能编程助手类的项目吗",
     "category": "简历素材", "keywords": ["LangGraph"]},
    {"id": "cv-techstack", "query": "候选人的技术栈有哪些",
     "category": "简历素材", "keywords": ["FastAPI"]},
    {"id": "cv-research", "query": "有深度学习相关的科研经历吗",
     "category": "简历素材", "keywords": ["睡眠纺锤波", "癫痫"]},
    # --- 跨域：问法里带另一个领域的词，考察抗干扰 ---
    {"id": "cross-kw", "query": "提示词工程和微调哪个更省钱",
     "source": "1.提示词工程", "keywords": ["微调"]},
    {"id": "cross-cnn", "query": "卷积和池化分别做什么",
     "source": "92.卷积神经网络", "keywords": ["卷积"]},
]


def kw_in_text(text: str, kw: str) -> bool:
    """关键词命中：含拉丁字母的按词边界匹配，纯中文直接子串。

    词边界是必须的——否则 "rag" 会命中 "Average Pooling"（真实踩坑）。
    右边界只排除字母不排除数字：让 "LangChain1.3" 能被 langchain 命中。
    """
    if re.search(r"[A-Za-z]", kw):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z])"
        return re.search(pattern, text, re.IGNORECASE) is not None
    return kw.lower() in text.lower()


def source_matches(hit: dict, case: dict) -> bool:
    meta = hit.get("metadata", {})
    if case.get("source") and case["source"] in str(meta.get("source", "")):
        return True
    if case.get("category") and meta.get("category") == case["category"]:
        return True
    return not case.get("source") and not case.get("category")


def is_hit(hit: dict, case: dict, loose: bool = False) -> bool:
    """strict：来源/类别对得上 **且** 关键词真的在这块文本里。
    loose：旧口径（来源匹配即算命中，或关键词子串命中），保留用于对比旧数字。
    """
    src_ok = source_matches(hit, case)
    if loose:
        return src_ok or any(kw_in_text_substring(hit.get("text", ""), kw)
                             for kw in case["keywords"])
    return src_ok and any(kw_in_text(hit.get("text", ""), kw) for kw in case["keywords"])


def kw_in_text_substring(text: str, kw: str) -> bool:
    """旧口径的子串匹配（仅 --loose 复现历史数字用）。"""
    return kw.lower() in text.lower()


def hit_rank(hits: list[dict], case: dict, loose: bool = False) -> int | None:
    for i, h in enumerate(hits, 1):
        if is_hit(h, case, loose):
            return i
    return None


def miss_reason(hits: list[dict], case: dict) -> str:
    """MISS 归因——决定了该修检索还是该修解析。"""
    if any(gen_ok(h, case) for h in hits):
        return "内容命中了但来源/类别不符 → 元数据或跨域用例设计问题"
    if any(source_matches(h, case) for h in hits):
        return "期望文档召回了，但含该内容的块没进 top-k → 召回失败（同文档其他块挤占名额）"
    return "期望文档整个没进 top-k → 召回失败（调检索；若库中确实没有则是用例过期）"


def hit_line(hit: dict) -> str:
    meta = hit.get("metadata", {})
    src = str(meta.get("source", "?"))
    tags = meta.get("tags", "")
    score = hit.get("rrf_score", hit.get("distance"))
    score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
    preview = " ".join(hit.get("text", "").split())[:58]
    return f"{src}[{tags}] | {score_txt} | {preview}"


def gen_ok(hit: dict, case: dict) -> bool:
    """生成侧命中：关键词出现即可（不看来源），用于区分"检索失败"和"生成失败"。"""
    return any(kw_in_text(hit.get("text", ""), kw) for kw in case["keywords"])


def evaluate(mode: str, loose: bool = False) -> dict:
    retriever = get_retriever()
    recalls = {1: [], 3: [], 5: []}
    rr_sum = 0.0
    cases = []
    for case in EVAL_SET:
        hits = retriever.search(case["query"], top_k=K, mode=mode)
        flags = [is_hit(h, case, loose) for h in hits]
        for k in recalls:
            recalls[k].append(1.0 if any(flags[:k]) else 0.0)
        rank = hit_rank(hits, case, loose)
        rr_sum += 1.0 / rank if rank else 0.0
        cases.append({
            "id": case["id"],
            "query": case["query"],
            "rank": rank,
            "reason": "" if rank else miss_reason(hits, case),
            "keyword_found_somewhere": any(gen_ok(h, case) for h in hits),
            "top": [hit_line(h) for h in hits],
        })
    n = len(EVAL_SET)
    return {
        "recall@1": sum(recalls[1]) / n,
        "recall@3": sum(recalls[3]) / n,
        "recall@5": sum(recalls[5]) / n,
        "MRR": rr_sum / n,
        "cases": cases,
    }


def check_corpus() -> list[str]:
    """语料一致性自检：用例是否还和当前知识库对得上。

    这步很有必要——曾经因为库里删了文档，用例改成了"永久命中"的假通过，
    指标看着满分、实际早就不测那个东西了。
    """
    chunks = get_vector_store().get_all_chunks()
    sources = sorted({str(c["metadata"].get("source", "")) for c in chunks})
    categories = sorted({str(c["metadata"].get("category", "")) for c in chunks})
    print(f"库内 {len(chunks)} 块 | 来源 {sources} | 类别 {categories}\n")
    problems: list[str] = []
    for case in EVAL_SET:
        if case.get("source") and not any(case["source"] in s for s in sources):
            problems.append(f"[{case['id']}] 期望来源「{case['source']}」不在库中")
        if case.get("category") and case["category"] not in categories:
            problems.append(f"[{case['id']}] 期望类别「{case['category']}」不在库中")
        absent = [kw for kw in case["keywords"]
                  if not any(kw_in_text(c["text"], kw) for c in chunks)]
        if absent:
            problems.append(f"[{case['id']}] 关键词 {absent} 在全库任何块里都不存在 → 用例已失效")
    return problems


def print_detail(results: dict[str, dict], modes: list[str]) -> None:
    print("\n========== 逐条诊断 ==========")
    for i, case in enumerate(EVAL_SET, 1):
        expect = []
        if case.get("source"):
            expect.append(f"来源含「{case['source']}」")
        if case.get("category"):
            expect.append(f"类别=「{case['category']}」")
        expect.append(f"内容含 {case['keywords']} 之一")
        print(f"\n[{i}/{len(EVAL_SET)}] {case['query']}  (#{case['id']})")
        print(f"    期望：{' + '.join(expect)}")
        for mode in modes:
            c = results[mode]["cases"][i - 1]
            if c["rank"]:
                print(f"    {mode:<7}: ✓ rank={c['rank']}  {c['top'][0]}")
            else:
                print(f"    {mode:<7}: ✗ MISS  {c['reason']}")
                for j, line in enumerate(c["top"], 1):
                    print(f"        {j}. {line}")
    for mode in modes:
        missed = [c for c in results[mode]["cases"] if c["rank"] is None]
        print(f"\n【{mode}】未命中 {len(missed)}/{len(EVAL_SET)}")
        for c in missed:
            print(f"  - {c['query']}  → {c['reason']}")


def print_table(results: dict[str, dict], modes: list[str]) -> None:
    metrics = ("recall@1", "recall@3", "recall@5", "MRR")
    if len(modes) == 2:
        header = f"{'指标':<10} {'纯向量 V1':>12} {'混合检索 V2':>12} {'变化':>10}"
    else:
        header = f"{'指标':<10} {modes[0]:>12}"
    print(header)
    print("-" * max(len(header.expandtabs()), 30))
    for m in metrics:
        if len(modes) == 2:
            v1, v2 = results[modes[0]][m], results[modes[1]][m]
            print(f"{m:<10} {v1:>11.1%} {v2:>12.1%} {v2 - v1:>+9.1%}")
        else:
            print(f"{m:<10} {results[modes[0]][m]:>11.1%}")


def print_diff(baseline: dict, current: dict) -> None:
    print("\n========== 与基线对比 ==========")
    b_modes = baseline.get("modes", {})
    for mode, c in current["modes"].items():
        b = b_modes.get(mode)
        if not b:
            print(f"[{mode}] 基线里没有该模式，跳过")
            continue
        print(f"\n[{mode}]")
        for m in ("recall@1", "recall@3", "recall@5", "MRR"):
            d = c[m] - b[m]
            print(f"  {m:<10} {b[m]:>7.1%} -> {c[m]:>7.1%}  ({d:+.1%}){'  ← 有变化' if abs(d) > 1e-9 else ''}")
        b_cases = {x["id"]: x["rank"] for x in baseline.get("cases", {}).get(mode, [])}
        c_cases = {x["id"]: x["rank"] for x in current.get("cases", {}).get(mode, [])}
        for label, cond in (("修好", lambda o, n: o is None and n is not None),
                            ("弄坏", lambda o, n: o is not None and n is None),
                            ("名次变动", lambda o, n: o and n and o != n)):
            items = [(cid, b_cases[cid], c_cases[cid]) for cid in c_cases
                     if cid in b_cases and cond(b_cases[cid], c_cases[cid])]
            if items:
                print(f"  {label}：")
                for cid, o, n in items:
                    print(f"    - {cid}: rank {o} -> {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="检索评测：向量 vs 混合")
    parser.add_argument("-v", "--detail", action="store_true", help="逐条诊断输出")
    parser.add_argument("--mode", choices=["vector", "hybrid"], help="只评测指定模式")
    parser.add_argument("--loose", action="store_true",
                        help="旧口径：来源匹配即算命中（复现历史数字用）")
    parser.add_argument("--check-corpus", action="store_true",
                        help="只做语料一致性自检：用例是否已过期")
    parser.add_argument("--save", metavar="PATH", help="把本次结果存档为 JSON")
    parser.add_argument("--baseline", metavar="PATH", help="与存档的基线 JSON 对比")
    args = parser.parse_args()

    if args.check_corpus:
        problems = check_corpus()
        print("用例一致性：" + ("全部 OK ✓" if not problems else f"发现 {len(problems)} 处问题"))
        for p in problems:
            print(f"  ⚠ {p}")
        if problems:
            print("\n提示：用例失效时指标会虚高（假通过）。请先修用例，再谈调优。")
        return

    modes = [args.mode] if args.mode else ["vector", "hybrid"]
    criterion = "loose（旧口径）" if args.loose else "strict（来源+内容双条件）"
    print(f"评测集：{len(EVAL_SET)} 条查询 | top{K} | 命中判定：{criterion}")
    results = {m: evaluate(m, args.loose) for m in modes}

    metrics_only = {m: {k: v for k, v in r.items() if k != "cases"} for m, r in results.items()}
    print()
    print_table(results, modes)
    step = 1.0 / len(EVAL_SET)
    print(f"\n注：1 条用例 = {step:.1%}，{len(EVAL_SET)} 条样本下小于 ±{2 * step:.0%} 的差异"
          f"（≤2 条用例）属噪声，不足以支持结论——先把评测集扩到 30~50 条再谈小幅提升。")
    if args.detail:
        print_detail(results, modes)

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "k": K,
        "criterion": "loose" if args.loose else "strict",
        "modes": metrics_only,
        "cases": {m: results[m]["cases"] for m in modes},
    }
    if args.save:
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n已存档：{args.save}")
    if args.baseline:
        print_diff(json.loads(Path(args.baseline).read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    main()
