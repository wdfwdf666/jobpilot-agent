"""Embedding 客户端：阿里云百炼 text-embedding-v4（Qwen3-Embedding 系列）。

面试考点（这些都是真踩过的坑，讲出来很有说服力）：
1. 为什么用 Qwen text-embedding-v4 而不是 OpenAI text-embedding-3？
   -> 中文语义更准、国内直连无网络问题、¥0.07/百万 token、维度可调（2048/1536/1024/768/512/256/128/64）。
2. 为什么必须分批？百炼限制：v3/v4 单次请求最多 10 条文本，每条最多 8192 token。
   知识库动辄几百块，不分批直接 400 报错。
3. 为什么显式指定 dimensions？维度决定存储成本与检索精度，且必须与已入库向量保持一致
   —— 换维度等于重建库，所以写进配置并记录。
"""
import threading

from openai import OpenAI

from app.config import Settings, get_settings

# .env.example 里的占位符，视为"未配置"
PLACEHOLDERS = {"sk-xxx", "sk-xxx-xxx", "your-api-key"}


def _is_real_key(value: str) -> bool:
    return bool(value) and value.strip() not in PLACEHOLDERS


def _resolve_api_key(settings: Settings) -> str:
    """百炼一份 Key 同时适用于 LLM 与 Embedding：EMBED_API_KEY 未配（或仍是占位符）时回退用 LLM Key。"""
    if _is_real_key(settings.embed_api_key):
        return settings.embed_api_key
    if _is_real_key(settings.llm_api_key):
        return settings.llm_api_key
    return ""


class EmbeddingClient:
    def __init__(self) -> None:
        settings = get_settings()
        api_key = _resolve_api_key(settings)
        if not api_key:
            raise RuntimeError("Embedding 无可用 API Key：请在 .env 填 LLM_API_KEY 或 EMBED_API_KEY")
        self._client = OpenAI(api_key=api_key, base_url=settings.embed_base_url)
        self._model = settings.embed_model
        self._dimensions = settings.embed_dimensions
        self._batch_size = settings.embed_batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，自动按 batch_size 分批。返回顺序与输入严格一致。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            resp = self._client.embeddings.create(
                input=batch,
                model=self._model,
                dimensions=self._dimensions,
            )
            # 按 index 排序，避免返回顺序与输入不一致导致向量错位
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dimensions(self) -> int:
        return self._dimensions


# --- 进程级单例 -------------------------------------------------------------
# 两个理由：
# 1. 性能：OpenAI 客户端内部持有 httpx 连接池，每请求新建 = 每请求重建连接池，
#    并发下既浪费又容易触发连接竞争。
# 2. 正确性：新版 openai SDK 对 `openai.resources.embeddings` 做惰性导入，
#    首次访问 client.embeddings 才真正 import；若这一步落在请求线程里且多个线程
#    同时进行，会触发 CPython 的模块锁死锁检测（见 app/warmup.py）。
_CLIENT: "EmbeddingClient | None" = None
_CLIENT_LOCK = threading.Lock()


def get_embedding_client() -> "EmbeddingClient":
    """取进程级共享的 Embedding 客户端。"""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = EmbeddingClient()
    return _CLIENT
