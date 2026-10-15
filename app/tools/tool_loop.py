"""Function Calling 工具循环：LLM 自主决定何时联网（P1）。

为什么循环写在「节点内部」而不是摊进 LangGraph 图（面试考点）：
工具调用是单个 Agent 的"思考-行动"内部过程。若把它摊成图里的
`agent -> tools -> agent` 条件环，四个 Agent 各要新增两类节点，State 还得承载
工具消息与步数，图会迅速膨胀且难以单测。放在节点内，图仍是"一进一出"的清晰结构，
工具细节对编排层完全透明。

三道防线防失控：
1. `max_steps` 限制循环轮数（默认 3），到顶就摘掉工具强制收尾；
2. Key 未配置时压根不把工具挂给模型，避免"每轮都想搜、每轮收到 error"白烧 token；
3. 工具异常转成 error 文本回喂，让模型自行降级，而不是抛穿整条链路。

流式取舍：工具决策轮走非流式（见 `llm.chat_with_tools`），最终回答仍逐字推送。
"""
import json
from dataclasses import dataclass, field
from typing import Any

from app.agents import llm
from app.tools.web_search import (
    WEB_SEARCH_TOOL,
    execute_tool_call,
    search_available,
)

MAX_STEPS = 3
CHUNK_SIZE = 64  # 非流式结果的分块推送粒度，与 jd_analyst 的摘要推送保持一致

GENERAL_SYSTEM = """你是求职助手，可以调用 web_search 联网查询公开信息。
只在需要「用户没有提供、且你无法凭常识确定」的实时事实时才搜索：某公司的业务与近期动态、
某岗位的市场行情、某技术或产品的最新情况。
用户已给出的 JD、简历与知识库内容不要联网查（那些属于私有资料）；不需要搜索就直接回答。
搜索失败时，用已有信息回答并说明未能联网，不要编造。"""


@dataclass
class ToolRun:
    """一次带工具对话的结果。"""

    text: str
    web_results: list[dict] = field(default_factory=list)
    tool_calls: int = 0  # 实际发起的工具调用次数（不是循环轮数）

    @property
    def used_tools(self) -> bool:
        return self.tool_calls > 0


def run_with_tools(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    writer: Any = None,
    max_steps: int = MAX_STEPS,
    prefix: str = "",
    only_if_used: bool = False,
) -> ToolRun:
    """带工具的对话循环，返回最终回答与联网命中的网页。

    `prefix` 只在实际调用过工具时才拼到回答前面（用于"岗位背景补充"这类小标题，
    没搜到东西时不会留下一个空标题）。

    `only_if_used`：模型判断无需搜索时静默丢弃它的回答。jd_analyst 只要"联网补充"
    这一段，不想把模型"无需补充"之类的客套话拼进岗位分析结果里。
    """
    convo: list[dict[str, Any]] = []
    if system:
        convo.append({"role": "system", "content": system})
    convo.extend(messages)

    # 没有搜索 Key：不挂工具，直接退化成普通流式对话
    if not search_available():
        return ToolRun(text=_stream_and_emit(convo, writer))

    results: list[dict] = []
    called = 0

    for _ in range(max_steps):
        msg = llm.chat_with_tools(convo, [WEB_SEARCH_TOOL])
        tool_calls = list(getattr(msg, "tool_calls", None) or [])

        if not tool_calls:
            if only_if_used and not called:
                # 调用方只要"联网补充"，模型判断无需搜索时的回答直接静默丢弃
                return ToolRun(text="", web_results=results, tool_calls=called)
            # 模型决定不调用工具：这一轮的非流式内容就是最终回答
            head = prefix if (prefix and called) else ""
            body = msg.content or ""
            if head:
                _emit(writer, {"delta": head})
            _push_in_chunks(body, writer)
            return ToolRun(text=head + body, web_results=results, tool_calls=called)

        # 把 assistant 的 tool_calls 原样带回上下文，否则下一轮会报消息顺序错误
        convo.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            called += 1
            _emit(writer, {"status": f"正在联网搜索：{_peek_query(tc)}"})
            content, hits = execute_tool_call(tc.function.name, tc.function.arguments)
            results.extend(hits)
            convo.append({"role": "tool", "tool_call_id": tc.id, "content": content})

    # 步数用尽：chat_stream 不带 tools，模型无法再调用工具，必须基于已有信息收尾
    convo.append({"role": "user", "content": "请基于以上信息直接给出最终回答，不要再调用工具。"})
    head = prefix if (prefix and called) else ""
    if head:
        _emit(writer, {"delta": head})
    tail = _stream_and_emit(convo, writer)
    return ToolRun(text=head + tail, web_results=results, tool_calls=called)


def _emit(writer: Any, event: dict) -> None:
    if writer is not None:
        writer(event)


def _push_in_chunks(text: str, writer: Any) -> None:
    """非流式结果分块推送：前端依然能渐进渲染，不必等整段生成完。"""
    for i in range(0, len(text), CHUNK_SIZE):
        _emit(writer, {"delta": text[i:i + CHUNK_SIZE]})


def _stream_and_emit(messages: list[dict[str, Any]], writer: Any) -> str:
    parts: list[str] = []
    for delta in llm.chat_stream(messages):
        parts.append(delta)
        _emit(writer, {"delta": delta})
    return "".join(parts)


def _peek_query(tool_call: Any) -> str:
    """从工具参数里取出 query，仅用于前端状态提示，解析失败不影响主流程。"""
    try:
        return str(json.loads(tool_call.function.arguments or "{}").get("query", ""))[:30]
    except Exception:  # noqa: BLE001
        return "…"
