"""LLM 客户端封装：阿里云百炼 Qwen（OpenAI 兼容接口），换模型只改 .env。

百炼的两个硬约束（踩过才知道，面试讲出来是加分项）：
1. response_format={"type":"json_object"} 要求 messages 里必须出现 "json" 字样，
   否则直接报错：'messages' must contain the word 'json'...。这里做了自动兜底。
2. 思考模式（enable_thinking=True）与 json_object 互斥，同时用会报 InvalidParameter。
   所以抽取类任务（json_mode=True）强制关闭思考。
"""
from typing import Any, Iterator

from openai import OpenAI

from app.config import get_settings


def get_llm() -> OpenAI:
    settings = get_settings()
    key = (settings.llm_api_key or "").strip()
    if not key or key in {"sk-xxx", "your-api-key"}:
        raise RuntimeError("LLM_API_KEY 未配置，请先在 .env 填写百炼 API Key")
    return OpenAI(api_key=key, base_url=settings.llm_base_url)


def _build_kwargs(messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
    settings = get_settings()
    kwargs: dict[str, Any] = {}

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        # 兜底：提示词里没有 "json" 时自动补一句，避免百炼直接报错
        if not any("json" in m.get("content", "").lower() for m in messages):
            messages = messages + [{"role": "user", "content": "请以 JSON 格式输出。"}]
        # 结构化输出与思考模式互斥，这里强制关闭
        kwargs["extra_body"] = {"enable_thinking": False}
    elif settings.llm_enable_thinking is not None:
        kwargs["extra_body"] = {"enable_thinking": settings.llm_enable_thinking}

    return kwargs | {"messages": messages}


def chat(messages: list[dict[str, str]], json_mode: bool = False, temperature: float = 0.3) -> str:
    """同步调用，返回完整文本。json_mode=True 时强制 JSON 输出（结构化抽取用）。"""
    settings = get_settings()
    kwargs = _build_kwargs(messages, json_mode)
    resp = get_llm().chat.completions.create(
        model=settings.llm_model, temperature=temperature, **kwargs
    )
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict[str, str]], json_mode: bool = False) -> Iterator[str]:
    """流式调用，逐段 yield 文本增量（SSE 用）。"""
    settings = get_settings()
    kwargs = _build_kwargs(messages, json_mode)
    stream = get_llm().chat.completions.create(
        model=settings.llm_model, temperature=0.3, stream=True, **kwargs
    )
    for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta.content
        if delta:
            yield delta
