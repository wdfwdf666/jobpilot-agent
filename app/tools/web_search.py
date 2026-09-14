"""联网搜索工具（P1）：Tavily API，供 Agent 通过 Function Calling 自主调用。"""
import httpx

from app.config import get_settings


def web_search(query: str, max_results: int = 5) -> list[dict]:
    settings = get_settings()
    if not settings.tavily_api_key:
        return [{"error": "TAVILY_API_KEY 未配置"}]
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
