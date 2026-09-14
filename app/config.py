"""全局配置。所有密钥和路径从 .env 读取，代码里不出现任何密钥。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # LLM（OpenAI 兼容接口）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # Embedding
    embed_api_key: str = ""
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_model: str = "BAAI/bge-m3"

    # 联网搜索
    tavily_api_key: str = ""

    # 存储
    chroma_dir: str = "data/chroma"
    uploads_dir: str = "data/uploads"
    collection_name: str = "jobpilot"

    # 分块参数（面试考点：块太大检索不精、太小上下文断裂）
    chunk_size: int = 500
    chunk_overlap: int = 120

    @property
    def chroma_path(self) -> Path:
        return BASE_DIR / self.chroma_dir

    @property
    def uploads_path(self) -> Path:
        return BASE_DIR / self.uploads_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
