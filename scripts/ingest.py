"""知识入库 CLI：
python scripts/ingest.py path/to/file.md --category 八股文 --tags python,并发
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.chunker import chunk_text  # noqa: E402
from app.rag.embeddings import EmbeddingClient  # noqa: E402
from app.rag.loader import load_text  # noqa: E402
from app.rag.vectorstore import VectorStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="把知识文件导入 JobPilot 知识库")
    parser.add_argument("path", help="文件路径（md/txt/pdf/docx）")
    parser.add_argument("--category", default="通用", help="类别：八股文/项目笔记/行业认知/简历素材")
    parser.add_argument("--tags", default="", help="逗号分隔标签，如 python,并发")
    args = parser.parse_args()

    text = load_text(args.path)
    chunks = chunk_text(text)
    print(f"[1/3] 解析完成：{len(text)} 字符 -> {len(chunks)} 块")
    embeddings = EmbeddingClient().embed(chunks)
    print("[2/3] 向量化完成")
    result = VectorStore().add_chunks(
        chunks, embeddings, source=Path(args.path).name,
        category=args.category, tags=[t for t in args.tags.split(",") if t],
    )
    print(f"[3/3] 入库完成：新增 {result['added']} 块，去重跳过 {result['skipped']} 块")


if __name__ == "__main__":
    main()
