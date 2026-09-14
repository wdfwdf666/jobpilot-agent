"""Embedding 客户端：OpenAI 兼容接口（默认 SiliconFlow 的 bge-m3）。

面试考点：为什么选 bge-m3 而不是 OpenAI embedding？
- 中文效果好、支持 8k 长度、可本地/国内 API 部署（数据合规 + 成本）。
"""
from openai import OpenAI

from app.config import get_settings


class EmbeddingClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.embed_api_key:
            raise RuntimeError("EMBED_API_KEY 未配置，请先填写 .env")
        self._client = OpenAI(api_key=settings.embed_api_key, base_url=settings.embed_base_url)
        self._model = settings.embed_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。注意 batch 有上限，超长列表由调用方分批。"""
        resp = self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in resp.data]
