"""工具循环测试：用 fake LLM 覆盖各条路径，不消耗真实 API 额度。

覆盖：
1. 模型不调用工具，直接回答
2. 调用一次工具后回答（并校验 assistant.tool_calls 原样回放、tool 消息顺序正确）
3. 工具报错 -> error 回喂 -> 模型降级作答
4. 达到 max_steps 上限 -> 摘掉工具强制收尾
5. 未配置搜索 Key -> 退化成普通流式对话，且工具不挂给模型
6. only_if_used：模型判断无需搜索时静默丢弃回答
7. 流式事件：status 与 delta 都推给了前端
"""
from types import SimpleNamespace

import pytest

from app.tools import tool_loop


class FakeToolCall:
    def __init__(self, name: str = "web_search", arguments: str = '{"query": "博查"}',
                 call_id: str = "call_1"):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


def fake_msg(content: str = "", tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


@pytest.fixture(autouse=True)
def search_ready(monkeypatch):
    """默认搜索可用；需要"未配 Key"的用例自行覆盖。"""
    monkeypatch.setattr(tool_loop, "search_available", lambda: True)


def test_answers_without_calling_tool(monkeypatch):
    calls = {"n": 0}

    def fake_chat_with_tools(messages, tools):
        calls["n"] += 1
        return fake_msg(content="这个问题不需要联网")

    monkeypatch.setattr(tool_loop.llm, "chat_with_tools", fake_chat_with_tools)

    run = tool_loop.run_with_tools([{"role": "user", "content": "你好"}])

    assert run.text == "这个问题不需要联网"
    assert run.used_tools is False
    assert calls["n"] == 1  # 只调一次，没有多余的轮次


def test_calls_tool_then_answers(monkeypatch):
    seen: dict = {}
    captured: list[list] = []
    replies = [
        fake_msg(tool_calls=[FakeToolCall(arguments='{"query": "某某公司"}')]),
        fake_msg(content="该公司主营业务是 X"),
    ]

    def fake_chat_with_tools(messages, tools):
        captured.append(list(messages))
        return replies.pop(0)

    def fake_execute(name, arguments):
        seen["name"] = name
        seen["arguments"] = arguments
        return '{"ok": true}', [{"title": "T", "url": "https://x.com", "content": "C"}]

    monkeypatch.setattr(tool_loop.llm, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(tool_loop, "execute_tool_call", fake_execute)

    run = tool_loop.run_with_tools([{"role": "user", "content": "这家公司做什么"}])

    assert run.text == "该公司主营业务是 X"
    assert run.tool_calls == 1
    assert run.used_tools is True
    assert seen["name"] == "web_search"
    assert seen["arguments"] == '{"query": "某某公司"}'
    assert run.web_results[0]["url"] == "https://x.com"

    # 第二轮上下文必须包含 assistant(tool_calls) 与配对的 tool 消息，
    # 否则 OpenAI 兼容接口会报消息顺序错误
    second = captured[1]
    roles = [m["role"] for m in second]
    assert roles == ["user", "assistant", "tool"]
    assert second[1]["tool_calls"][0]["id"] == "call_1"
    assert second[2]["tool_call_id"] == "call_1"


def test_tool_error_is_fed_back(monkeypatch):
    replies = [fake_msg(tool_calls=[FakeToolCall()]), fake_msg(content="联网失败了，我用已有信息回答")]
    monkeypatch.setattr(tool_loop.llm, "chat_with_tools", lambda m, t: replies.pop(0))
    monkeypatch.setattr(tool_loop, "execute_tool_call",
                        lambda n, a: ('{"error": "搜索失败 TimeoutError"}', []))

    run = tool_loop.run_with_tools([{"role": "user", "content": "q"}])

    assert run.tool_calls == 1  # 确实尝试过
    assert run.web_results == []  # 但没有命中
    assert "已有信息" in run.text  # 模型收到 error 后降级作答，链路没断


def test_max_steps_forces_finish(monkeypatch):
    """模型每轮都要求调工具、永不停止时，到上限必须摘掉工具强制收尾。"""
    monkeypatch.setattr(tool_loop.llm, "chat_with_tools",
                        lambda m, t: fake_msg(tool_calls=[FakeToolCall()]))
    monkeypatch.setattr(tool_loop, "execute_tool_call",
                        lambda n, a: ('{"ok": 1}', [{"url": "https://a.com"}]))

    streamed: dict = {}

    def fake_stream(messages):
        streamed["messages"] = messages
        yield "收尾内容"

    monkeypatch.setattr(tool_loop.llm, "chat_stream", fake_stream)

    run = tool_loop.run_with_tools([{"role": "user", "content": "q"}], max_steps=2)

    assert run.tool_calls == 2  # 正好用满上限
    assert run.text.endswith("收尾内容")
    # 收尾调用不带 tools，并额外追加一条"不要再调用工具"的指令
    assert streamed["messages"][-1]["content"].startswith("请基于以上信息")


def test_falls_back_without_key(monkeypatch):
    """没配 Key 时不挂工具，也不该产生任何工具决策调用。"""
    monkeypatch.setattr(tool_loop, "search_available", lambda: False)
    monkeypatch.setattr(tool_loop.llm, "chat_with_tools",
                        lambda m, t: pytest.fail("未配 Key 时不应该挂工具"))
    monkeypatch.setattr(tool_loop.llm, "chat_stream", lambda messages: iter(["直接", "回答"]))

    run = tool_loop.run_with_tools([{"role": "user", "content": "q"}])

    assert run.text == "直接回答"
    assert run.used_tools is False


def test_only_if_used_discards_answer(monkeypatch):
    """JD 补充轮：模型判断无需搜索时，它的回答要整段丢弃，不留空标题。"""
    monkeypatch.setattr(tool_loop.llm, "chat_with_tools", lambda m, t: fake_msg(content="无需补充"))

    run = tool_loop.run_with_tools(
        [{"role": "user", "content": "q"}], prefix="\n\n**标题**\n\n", only_if_used=True
    )

    assert run.text == ""
    assert run.used_tools is False


def test_prefix_only_appears_when_tool_used(monkeypatch):
    replies = [fake_msg(tool_calls=[FakeToolCall()]), fake_msg(content="补充内容")]
    monkeypatch.setattr(tool_loop.llm, "chat_with_tools", lambda m, t: replies.pop(0))
    monkeypatch.setattr(tool_loop, "execute_tool_call",
                        lambda n, a: ('{"ok": 1}', [{"url": "https://a.com"}]))

    run = tool_loop.run_with_tools(
        [{"role": "user", "content": "q"}], prefix="**岗位背景补充**\n\n"
    )

    assert run.text.startswith("**岗位背景补充**")
    assert run.text.endswith("补充内容")


def test_emits_status_and_deltas(monkeypatch):
    """前端要靠 status 显示"正在联网搜索"，靠 delta 渐进渲染。"""
    events: list[dict] = []
    replies = [
        fake_msg(tool_calls=[FakeToolCall(arguments='{"query": "某公司 业务"}')]),
        fake_msg(content="答案"),
    ]
    monkeypatch.setattr(tool_loop.llm, "chat_with_tools", lambda m, t: replies.pop(0))
    monkeypatch.setattr(tool_loop, "execute_tool_call",
                        lambda n, a: ('{"ok": 1}', [{"url": "https://a.com"}]))

    tool_loop.run_with_tools([{"role": "user", "content": "q"}], writer=events.append)

    statuses = [e["status"] for e in events if "status" in e]
    deltas = "".join(e["delta"] for e in events if "delta" in e)
    assert any("某公司 业务" in s for s in statuses)
    assert deltas == "答案"
