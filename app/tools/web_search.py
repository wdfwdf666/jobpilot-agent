"""联网搜索工具（P1）：默认博查 BochaAI（国内），Tavily 可选兜底。

选型：博查是 DeepSeek 等国内 AI 应用广泛使用的搜索 API——中文效果好、
注册送 2000 次免费额度、¥3.6/千次（约为 Tavily 的 1/6）。
两个 provider 归一化成相同的返回结构 [{title, url, content}]，
上层（Agent 的 Function Calling）不感知底层是哪家。

面试考点：为什么归一化？——搜索服务商可能随时涨价/停服（Bing Search API
已停用），接口层隔离后换供应商只改一个 provider 字符串。
"""
import json

import httpx

from app.config import get_settings


def web_search(query: str, max_results: int = 5, provider: str | None = None) -> list[dict]:
    """联网搜索。provider 为空时取配置默认值；未配置对应 Key 时返回友好错误。"""
    settings = get_settings()
    provider = (provider or settings.search_provider).lower()
    if provider == "bocha":
        return _bocha(query, max_results)
    if provider == "tavily":
        return _tavily(query, max_results)
    return [{"error": f"未知搜索 provider: {provider}（支持 bocha / tavily）"}]


def _bocha(query: str, max_results: int) -> list[dict]:
    settings = get_settings()
    if not settings.bocha_api_key:
        return [{"error": "BOCHA_API_KEY 未配置（博查开放平台注册领取，送 2000 次）"}]
    resp = httpx.post(
        settings.bocha_search_url,
        headers={
            "Authorization": f"Bearer {settings.bocha_api_key}",
            "Content-Type": "application/json",
        },
        json={"query": query, "count": max_results, "summary": True},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 200:
        return [{"error": f"博查返回异常 code={payload.get('code')}: {payload.get('message', '')}"}]
    # 响应结构：data.webPages.value[]，字段 name/url/summary/snippet
    pages = (payload.get("data") or {}).get("webPages") or {}
    return [
        {
            "title": p.get("name", ""),
            "url": p.get("url", ""),
            # summary 是博查 AI 提炼的摘要，退化为 snippet
            "content": (p.get("summary") or p.get("snippet") or "")[:500],
        }
        for p in pages.get("value", [])
    ]


def _tavily(query: str, max_results: int) -> list[dict]:
    settings = get_settings()
    if not settings.tavily_api_key:
        return [{"error": "TAVILY_API_KEY 未配置（或把 search_provider 改为 bocha）"}]
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": settings.tavily_api_key, "query": query, "max_results": max_results},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {"title": r["title"], "url": r["url"], "content": r["content"][:500]}
        for r in resp.json().get("results", [])
    ]


# --- Function Calling：工具声明与分发 ------------------------------------------
# 工具描述刻意写得克制。描述过宽时模型会对着用户已给的简历/JD 反复联网查询，
# 既烧 token，又把知识库里的确切答案换成网上搜来的泛泛之谈。
WEB_SEARCH_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "联网搜索公开信息。仅用于核查用户没有提供、且无法凭常识确定的事实，"
            "例如某公司的主营业务与近期动态、某岗位的市场行情、某技术或产品的最新情况。"
            "用户已提供的 JD、简历、知识库内容不要用它查询。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，中文，尽量具体（带上公司名 / 岗位名命中率更高）",
                }
            },
            "required": ["query"],
        },
    },
}

TOOL_NAME: str = WEB_SEARCH_TOOL["function"]["name"]


def search_available() -> bool:
    """当前 provider 的 Key 是否可用。

    没配 Key 时压根不把工具挂给模型——否则模型每轮都想搜、每轮收到一条 error，
    白烧 token 还污染回答。这是刻意的降级，不是遗漏。
    """
    settings = get_settings()
    provider = (settings.search_provider or "").lower()
    if provider == "bocha":
        return bool(settings.bocha_api_key)
    if provider == "tavily":
        return bool(settings.tavily_api_key)
    return False


def execute_tool_call(name: str, arguments: str) -> tuple[str, list[dict]]:
    """执行一次工具调用，返回 (回喂给模型的文本, 命中的网页列表)。

    任何异常都在这里兜住并转成结构化 error 回喂，让模型自己决定降级策略
    （放弃联网、改用已有信息回答），而不是让异常抛穿整条对话链路。
    对应面试题"工具调用失败了怎么办"：不重试、不崩溃，把错误交给模型处理。
    """
    if name != TOOL_NAME:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False), []

    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"工具参数不是合法 JSON: {exc}"}, ensure_ascii=False), []

    query = str(args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "缺少 query 参数"}, ensure_ascii=False), []

    try:
        results = web_search(query)
    except Exception as exc:  # noqa: BLE001 —— 网络 / HTTP / 解析异常一律降级
        return json.dumps(
            {"error": f"搜索失败 {type(exc).__name__}: {exc}"}, ensure_ascii=False
        ), []

    # provider 层用 [{"error": ...}] 表达配置或业务错误，同样按失败回喂
    if results and results[0].get("error"):
        return json.dumps(results[0], ensure_ascii=False), []

    return json.dumps(results, ensure_ascii=False), results
