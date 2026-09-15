"""RAG 链路端到端自检：入库 -> 检索 -> 打印命中结果。

用法：
    python scripts/check_rag.py                # 用内置示例知识跑一遍
    python scripts/check_rag.py path/to.md      # 用自己的文件跑

验证点：分块是否合理、向量化是否成功、检索能否命中语义相关的内容。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag.chunker import chunk_text  # noqa: E402
from app.rag.embeddings import EmbeddingClient  # noqa: E402
from app.rag.loader import load_text  # noqa: E402
from app.rag.vectorstore import VectorStore  # noqa: E402

SAMPLE = ROOT / "data/samples/sample_python_notes.md"
QUERIES = ["GIL 会导致多线程不能利用多核吗", "协程里能不能用 time.sleep"]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE

    text = load_text(path)
    chunks = chunk_text(text)
    print(f"[1/3] 解析 {path.name}：{len(text)} 字符 -> {len(chunks)} 块")

    embeddings = EmbeddingClient().embed(chunks)
    print(f"[2/3] 向量化完成：{len(embeddings)} 条，维度 {len(embeddings[0])}")

    store = VectorStore()
    result = store.add_chunks(chunks, embeddings, source=path.name, category="八股文", tags=["示例"])
    print(f"[3/3] 入库：新增 {result['added']} 块，去重跳过 {result['skipped']} 块；库内共 {store.count()} 块")

    ok = True
    for query in QUERIES:
        hits = store.query(EmbeddingClient().embed_one(query), top_k=2)
        print(f"\n查询：{query}")
        for hit in hits:
            preview = hit["text"].replace("\n", " ")[:60]
            print(f"  - 距离 {hit['distance']:.4f} | {hit['metadata']['category']} | {preview}...")
        if not hits:
            ok = False
            print("  ! 无命中")

    print("\n结果:", "RAG 链路连通 ✅" if ok else "检索异常 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
