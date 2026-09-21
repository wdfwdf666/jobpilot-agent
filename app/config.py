"""全局配置。所有密钥和路径从 .env 读取，代码里不出现任何密钥。"""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # env_ignore_empty：.env 里的 `KEY=`（留空）视为未设置，走默认值，
    # 否则空字符串会在 bool/int 字段上直接抛 ValidationError（真实踩坑）。
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # LLM（阿里云百炼 DashScope，OpenAI 兼容接口）
    # 选型理由：qwen-plus 上下文 1M、价格约 ¥0.8/百万输入 token（非思考模式），
    # 能力/成本均衡，适合本项目这种"多轮 + 长上下文 JD"的场景。
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    # 三态：None=不传该参数用模型默认；True/False=显式开关
    # 注意：思考模式与 response_format=json_object 互斥（百炼限制）
    llm_enable_thinking: bool | None = None

    # Embedding（同为百炼，text-embedding-v4 属 Qwen3-Embedding 系列）
    embed_api_key: str = ""
    embed_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embed_model: str = "text-embedding-v4"
    embed_dimensions: int = 1024
    # 百炼限制：text-embedding-v3/v4 单次请求最多 10 条文本，必须分批
    embed_batch_size: int = 10

    # 联网搜索（P1）：默认博查 BochaAI（国内，注册送 2000 次，之后 ¥3.6/千次），
    # 可切换回 Tavily 兜底。博查接口：POST /v1/web-search，Bearer 鉴权，返回 webPages.value[]。
    search_provider: str = "bocha"  # bocha | tavily
    bocha_api_key: str = ""
    bocha_search_url: str = "https://api.bochaai.com/v1/web-search"
    tavily_api_key: str = ""

    # 存储
    chroma_dir: str = "data/chroma"
    uploads_dir: str = "data/uploads"
    collection_name: str = "jobpilot"

    # 分块参数（面试考点：块太大检索不精、太小上下文断裂）
    chunk_size: int = 500
    chunk_overlap: int = 120

    # 检索模式：vector=纯向量（V1）；hybrid=BM25+向量 RRF 融合（V2，默认）
    retrieval_mode: str = "hybrid"
    # 混合检索可调参数（用 scripts/eval_retrieval.py 做网格搜索，别拍脑袋调）。
    # rrf_k 越小越"奖励头部名次"，越大越平权；recall_per_channel 决定融合前的候选池，
    # 太小可能让某一路的正确答案没进池子，太大会引入噪声又拖慢融合。
    rrf_k: int = 60
    recall_per_channel: int = 20

    @field_validator("llm_enable_thinking", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """兜底：空串/空白串按"未设置"处理，避免 .env 留空导致启动即崩。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def chroma_path(self) -> Path:
        return BASE_DIR / self.chroma_dir

    @property
    def uploads_path(self) -> Path:
        return BASE_DIR / self.uploads_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
