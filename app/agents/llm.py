"""LLM 客户端封装：OpenAI 兼容接口，换模型只改 .env，不改代码。"""
from typing import Any, Iterator

from openai import OpenAI

from app.config import get_settings


def get_llm() -> OpenAI:
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY 未配置，请先填写 .env")
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def chat(messages: list[dict[str, str]], json_mode: bool = False) -> str:
    """同步调用，返回完整文本。json_mode=True 时强制 JSON 输出（结构化抽取用）。"""
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = get_llm().chat.completions.create(
        model=settings.llm_model, messages=messages, temperature=0.3, **kwargs
    )
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict[str, str]]) -> Iterator[str]:
    """流式调用，逐段 yield 文本增量（SSE 用）。"""
    settings = get_settings()
    stream = get_llm().chat.completions.create(
        model=settings.llm_model, messages=messages, temperature=0.3, stream=True
    )
    for event in stream:
        delta = event.choices[0].delta.content
        if delta:
            yield delta
