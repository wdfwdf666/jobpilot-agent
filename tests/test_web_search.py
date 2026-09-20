"""联网搜索工具测试：mock HTTP，不消耗真实 API 额度。"""
from app.config import get_settings
from app.tools import web_search as ws


def test_missing_key_returns_friendly_error(monkeypatch):
    """未配置 Key 时返回友好提示而不是抛异常（Agent 工具不能崩）。"""
    monkeypatch.setattr(get_settings(), "bocha_api_key", "")
    out = ws.web_search("test", provider="bocha")
    assert len(out) == 1
    assert "BOCHA_API_KEY" in out[0]["error"]


def test_unknown_provider(monkeypatch):
    monkeypatch.setattr(get_settings(), "bocha_api_key", "sk-test")
    out = ws.web_search("test", provider="bogus")
    assert "未知搜索 provider" in out[0]["error"]


def test_bocha_response_normalization(monkeypatch):
    """博查响应 data.webPages.value[] 归一化为 title/url/content。"""
    monkeypatch.setattr(get_settings(), "bocha_api_key", "sk-test")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "code": 200,
                "data": {
                    "webPages": {
                        "value": [
                            {"name": "标题A", "url": "https://a.com",
                             "summary": "摘要内容", "snippet": "片段"},
                            {"name": "标题B", "url": "https://b.com",
                             "snippet": "无 summary 时退化为 snippet"},
                        ]
                    }
                },
            }

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return FakeResp()

    monkeypatch.setattr(ws.httpx, "post", fake_post)
    out = ws.web_search("qwen plus", max_results=2, provider="bocha")
    assert captured["url"].endswith("/v1/web-search")
    assert captured["json"]["count"] == 2
    assert captured["json"]["summary"] is True
    assert out[0]["title"] == "标题A" and out[0]["content"] == "摘要内容"
    assert out[1]["content"] == "无 summary 时退化为 snippet"


def test_bocha_error_code_surfaces(monkeypatch):
    """博查业务错误（code != 200）要带出来，不能当成空结果静默吞掉。"""
    monkeypatch.setattr(get_settings(), "bocha_api_key", "sk-test")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 401, "message": "invalid api key"}

    monkeypatch.setattr(ws.httpx, "post", lambda url, **kw: FakeResp())
    out = ws.web_search("q", provider="bocha")
    assert "code=401" in out[0]["error"]
