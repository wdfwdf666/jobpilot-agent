"""联网搜索工具（P1）：默认博查 BochaAI（国内），Tavily 可选兜底。

选型：博查是 DeepSeek 等国内 AI 应用广泛使用的搜索 API——中文效果好、
注册送 2000 次免费额度、¥3.6/千次（约为 Tavily 的 1/6）。
两个 provider 归一化成相同的返回结构 [{title, url, content}]，
上层（Agent 的 Function Calling）不感知底层是哪家。

面试考点：为什么归一化？——搜索服务商可能随时涨价/停服（Bing Search API
已停用），接口层隔离后换供应商只改一个 provider 字符串。
"""
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
