"""简历入库 CLI：
python scripts/ingest_resume.py "C:/Users/xxx/王东风—AI Agent开发工程师.pdf" [--query "项目经历亮点"]

做三件事：
1. 解析简历（板块识别）并打印结构，方便确认解析质量；
2. 按板块分块 + 向量化入库（category=简历素材，tags=命中的板块名）；
3. 可选：立刻跑一个检索 + LLM 问答，验证"检索结果真的进了模型上下文"。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.embeddings import EmbeddingClient  # noqa: E402
from app.rag.loader import load_text  # noqa: E402
from app.rag.resume_parser import parse_resume, resume_summary  # noqa: E402
from app.rag.vectorstore import VectorStore  # noqa: E402

CATEGORY = "简历素材"


def main() -> None:
    parser = argparse.ArgumentParser(description="解析简历并入库（category=简历素材）")
    parser.add_argument("path", help="简历文件（pdf/docx/html/md/txt）")
    parser.add_argument("--source", default="", help="来源名，默认取文件名")
    parser.add_argument("--query", default="", help="入库后立刻用该问题验证 检索+LLM")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--replace", action="store_true",
                        help="先删除同源旧块再入库（简历改版后避免旧版本残留干扰检索）")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"文件不存在: {path}")
        raise SystemExit(1)

    raw = load_text(path)
    profile = parse_resume(raw)
    summary = resume_summary(profile)

    print(f"[1/4] 解析完成：{len(raw)} 字符")
    print(f"      姓名字段：{summary['name'] or '（未识别）'}  联系方式：{summary['contact'] or '（未识别）'}")
    print(f"      结构化：{'是（识别到板块）' if summary['structured'] else '否（PDF 丢标题，已降级全文分块）'}")
    for s in summary["sections"]:
        print(f"      - {s['name']}: {s['chars']} 字符 | {s['preview']}…")

    blocks = profile.to_blocks()
    print(f"[2/4] 分块完成：{len(blocks)} 块（板块内分块，块文本带【板块】前缀）")
    # 板块置信度告警：PDF 抽取常丢标题层级，整份正文挤进一个板块时
    # 板块标签就失去意义了（真实踩坑：整份简历被归到「专业技能」）
    total_chars = sum(len(s.content) for s in profile.sections) or 1
    dominant = max(profile.sections, key=lambda s: len(s.content))
    if not profile.structured or len(dominant.content) / total_chars > 0.7:
        print(f"      ⚠ 板块识别置信度低：{len(dominant.content) / total_chars:.0%} 的正文被归入"
              f"「{dominant.name}」，建议改用 md/docx 版本")
    if args.dry_run:
        print("dry-run 结束，未入库。")
        return

    embeddings = EmbeddingClient().embed([b["text"] for b in blocks])
    print("[3/4] 向量化完成")
    store = VectorStore()
    source = args.source or path.name
    if args.replace:
        store.delete_by_source(source)
        print(f"      已清除同源旧块：{source}")
    result = store.add_chunks(
        [b["text"] for b in blocks],
        embeddings,
        source=source,
        category=CATEGORY,
        tags=sorted({b["section"] for b in blocks}),          # 文档级：板块并集
        per_chunk_tags=[[b["section"]] for b in blocks],      # 块级：只带自己的板块
    )
    print(f"[4/4] 入库完成：新增 {result['added']} 块，去重跳过 {result['skipped']} 块")

    if args.query:
        from app.agents.resume_advisor import advise
        from app.rag.retriever import Retriever
        from app.schemas import RetrievedChunk

        hits = Retriever().search(args.query, top_k=4, category=CATEGORY)
        print(f"\n[验证] 检索「{args.query}」命中 {len(hits)} 块：")
        for h in hits:
            print(f"       [{h['metadata'].get('category')}] {h['text'][:70]}… (距离 {h['distance']:.3f})")
        chunks = [RetrievedChunk(text=h["text"], source=h["metadata"].get("source", ""),
                                 category=h["metadata"].get("category", "")) for h in hits]
        answer = advise(args.query, chunks)
        print(f"\n[LLM 回答]\n{answer}")


if __name__ == "__main__":
    main()
