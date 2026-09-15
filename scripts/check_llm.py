"""连通性自检：验证 .env 里的 Qwen LLM 与 Embedding 是否可用。

用法：
    python scripts/check_llm.py
两个都通了，说明 API Key、base_url、模型名、维度配置全部正确。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import llm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.rag.embeddings import EmbeddingClient  # noqa: E402


def main() -> int:
    s = get_settings()
    print(f"[配置] LLM: {s.llm_model} @ {s.llm_base_url}")
    print(f"[配置] Embedding: {s.embed_model} (dim={s.embed_dimensions}, batch={s.embed_batch_size})")

    ok = True

    # 1. LLM 普通对话
    try:
        reply = llm.chat([{"role": "user", "content": "只回复两个字：通了"}])
        print(f"[LLM] OK -> {reply.strip()[:40]}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[LLM] 失败: {exc}")

    # 2. LLM JSON 模式（结构化输出，抽取类 Agent 依赖）
    try:
        raw = llm.chat([{"role": "user", "content": '输出 JSON：{"status":"ok"}'}], json_mode=True)
        print(f"[LLM/json] OK -> {raw.strip()[:60]}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[LLM/json] 失败: {exc}")

    # 3. Embedding（含分批：故意传 13 条，超过单批 10 条上限）
    try:
        vectors = EmbeddingClient().embed([f"测试文本 {i}" for i in range(13)])
        dim = len(vectors[0])
        print(f"[Embedding] OK -> {len(vectors)} 条，维度 {dim}")
        if dim != s.embed_dimensions:
            ok = False
            print(f"[Embedding] 警告：返回维度 {dim} 与配置 {s.embed_dimensions} 不一致")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[Embedding] 失败: {exc}")

    print("\n结果:", "全部通过 ✅" if ok else "存在失败项 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
