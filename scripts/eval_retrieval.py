"""检索评测（D10-11）：纯向量 vs 混合检索的量化对比。

评测集：每条 = 一个真实用户问题 + 期望命中的文档/关键词。
指标：recall@1/@3/@5（top-k 内命中期望文档的比例）、MRR（排名质量）。
用法：
    python scripts/eval_retrieval.py
面试话术：评测先行——不拍脑袋说"混合检索更好"，而是跑出数字：
混合检索在专有名词类查询上的 recall@5 从 X% 提升到 Y%。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.retriever import get_retriever  # noqa: E402

# query -> 期望命中（source 子串匹配 or 关键词出现在块文本中即算命中）
EVAL_SET = [
    # --- 八股文：专有名词/缩写类（BM25 的主场，向量容易翻车）---
    {"query": "GIL 会影响多核性能吗", "source": "sample_python_notes", "keywords": ["GIL"]},
    {"query": "装饰器的本质是什么", "source": "sample_python_notes", "keywords": ["装饰器"]},
    {"query": "CNN 感受野怎么计算", "source": "92.卷积神经网络", "keywords": ["感受野"]},
    {"query": "卷积神经网络 padding 的作用", "source": "92.卷积神经网络", "keywords": ["padding"]},
    {"query": "池化层有什么用", "source": "92.卷积神经网络", "keywords": ["池化"]},
    {"query": "什么是空洞卷积", "source": "92.卷积神经网络", "keywords": ["空洞"]},
    {"query": "few-shot 提示词怎么写", "source": "1.提示词工程", "keywords": ["few-shot"]},
    {"query": "思维链 CoT 能解决什么问题", "source": "1.提示词工程", "keywords": ["思维链"]},
    {"query": "大模型的幻觉怎么用提示词缓解", "source": "1.提示词工程", "keywords": ["幻觉"]},
    # --- 简历素材：语义改写类（向量的主场）---
    {"query": "候选人做过 RAG 项目吗", "source": "王东风", "keywords": ["RAG"]},
    {"query": "求职者会哪些编程语言", "source": "王东风", "keywords": ["Python"]},
    {"query": "有深度学习相关的科研经历吗", "source": "王东风", "keywords": ["癫痫", "睡眠", "深度学习"]},
    # --- 跨域混合：既要字面命中又要语义泛化 ---
    {"query": "LangChain 的 Chain 底层是怎么实现的", "source": "LangChain", "keywords": ["Chain"]},
    {"query": "提示词工程和 RAG 的区别", "source": "1.提示词工程", "keywords": ["提示词"]},
]


def is_hit(hit: dict, case: dict) -> bool:
    """命中判定：source 子串匹配，或关键词出现在块文本（大小写不敏感）。"""
    src = str(hit.get("metadata", {}).get("source", ""))
    if case["source"] in src:
        return True
    text = hit.get("text", "").lower()
    return any(kw.lower() in text for kw in case["keywords"])


def evaluate(mode: str) -> dict:
    retriever = get_retriever()
    recalls = {1: [], 3: [], 5: []}
    rr_sum = 0.0
    for case in EVAL_SET:
        hits = retriever.search(case["query"], top_k=5, mode=mode)
        flags = [is_hit(h, case) for h in hits]
        for k in recalls:
            recalls[k].append(1.0 if any(flags[:k]) else 0.0)
        rr_sum += 1.0 / (flags.index(True) + 1) if True in flags else 0.0
    n = len(EVAL_SET)
    return {
        "recall@1": sum(recalls[1]) / n,
        "recall@3": sum(recalls[3]) / n,
        "recall@5": sum(recalls[5]) / n,
        "MRR": rr_sum / n,
    }


def main() -> None:
    print(f"评测集：{len(EVAL_SET)} 条查询\n")
    results = {}
    for mode in ("vector", "hybrid"):
        results[mode] = evaluate(mode)
    header = f"{'指标':<10} {'纯向量 V1':>12} {'混合检索 V2':>12} {'变化':>10}"
    print(header)
    print("-" * len(header.expandtabs()))
    for metric in ("recall@1", "recall@3", "recall@5", "MRR"):
        v1, v2 = results["vector"][metric], results["hybrid"][metric]
        delta = v2 - v1
        print(f"{metric:<10} {v1:>11.1%} {v2:>12.1%} {delta:>+9.1%}")


if __name__ == "__main__":
    main()
