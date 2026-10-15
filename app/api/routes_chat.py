"""聊天接口：SSE 真流式输出 + 人工审核中断（interrupt / resume）。

链路：节点内 LLM stream=True -> langgraph custom stream -> 这里逐事件转发 SSE。

面试考点 1：为什么用 SSE 而不是 WebSocket？
-> 单向推送场景下 SSE 更轻：走 HTTP、自动重连、代理友好，无需额外协议升级。

面试考点 2：人在环（human-in-the-loop）怎么接进 SSE？
-> 图在 interrupt 处停下，values 流里会出现 `__interrupt__`；这里把它翻译成一个
   `interrupt` SSE 事件发给前端（携带待确认内容），**不发 done**——一轮 SSE 就此结束。
   前端拿到人工决定后打 `POST /chat/resume`，服务端用 `Command(resume=...)` 驱动
   同一条 thread，翻译逻辑与 /stream 完全共用，所以前端只需要同一套帧解析。

面试考点 3：为什么会话历史不再需要进程内 dict？
-> 图状态（含 messages）由 checkpointer 落在 SQLite 里，按 thread_id 取回即可。
   重启进程、甚至换一个实例，历史都还在——这才是"多实例部署不丢会话"的正解。
"""
import json
from typing import Any, Iterator, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from app.agents.graph import app_graph, get_interviewer

router = APIRouter(prefix="/chat", tags=["chat"])

Mode = Literal["auto", "jd_analysis", "resume_advice", "mock_interview", "general_chat"]


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str
    mode: Mode = "auto"  # auto=Planner 关键词路由；显式指定则跳过路由（前端模式选择器）


class ResumeRequest(BaseModel):
    session_id: str = "default"
    # 人工审核结果：{"action": "approve"} 或 {"action": "edit", "hard_requirements": [...], ...}
    decision: dict[str, Any]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _thread_config(session_id: str) -> dict:
    """thread_id 就是 session_id：同一会话的中断与状态归属于同一条线程。"""
    return {"configurable": {"thread_id": session_id}}


def _interrupt_payload(interrupts: Any) -> dict:
    """把 `__interrupt__`（tuple[Interrupt]）解成可 JSON 化的 dict。

    Interrupt.value 就是节点里 interrupt(...) 传进去的内容；这里防御性处理，
    避免 LangGraph 版本差异导致前端拿到非对象。
    """
    first = interrupts[0] if isinstance(interrupts, (list, tuple)) and interrupts else interrupts
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"value": value}


def _run_stream(payload: Any, session_id: str) -> Iterator[str]:
    """把 LangGraph 的 custom/values 双通道流翻译成 SSE 帧（/stream 与 /resume 共用）。

    payload 是图输入 dict（新一轮）或 Command(resume=...)（恢复中断）。
    """
    reply_parts: list[str] = []
    final_state: dict = {}
    interrupted = False
    for mode, chunk in app_graph.stream(
        payload, config=_thread_config(session_id), stream_mode=["custom", "values"]
    ):
        if mode == "custom":
            if "delta" in chunk:
                reply_parts.append(chunk["delta"])
                yield _sse("delta", {"text": chunk["delta"]})
            elif "status" in chunk:
                yield _sse("status", {"text": chunk["status"]})
        else:  # values：每个节点结束后的全量 state，最后一个即最终态
            final_state = chunk
            interrupts = chunk.get("__interrupt__")
            if interrupts:
                interrupted = True
                yield _sse("interrupt", _interrupt_payload(interrupts))

    if interrupted:
        # 中断不是异常：这一轮就到这里，等前端带人工决定打 /chat/resume。
        # 不能发 done——发了前端会以为本轮正常结束（真实踩坑点）。
        return

    # 注意 final_state["reply"] 是"检查点里的当前值"，会跨轮残留；
    # 优先用本轮实际流出的文本，缺失时才回退，避免把上一轮回复当成本轮结果。
    reply = "".join(reply_parts) or final_state.get("reply", "") or "（空回复）"
    if final_state.get("artifacts"):
        yield _sse("artifacts", final_state["artifacts"])
    yield _sse("done", {"intent": final_state.get("intent", ""), "reply_chars": len(reply)})


def _streaming_response(gen: Iterator[str]) -> StreamingResponse:
    def guarded():
        try:
            yield from gen
        except Exception as exc:  # noqa: BLE001
            # 带上异常类型名：只发 str(exc) 时 KeyError 这类错误只剩一段引号文字，没法定位
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        guarded(),
        media_type="text/event-stream",
        # 禁用代理/浏览器缓冲，确保增量逐个到达前端
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    graph_input = {
        # 只传增量：messages 用 operator.add 累加，由 checkpointer 保存，
        # 不能把整段历史再传一遍（会平方级重复累积）。
        "messages": [{"role": "user", "content": req.message}],
        "user_input": req.message,
        "session_id": req.session_id,
        "force_intent": req.mode,
        "intent": "",
        "reply": "",
        "artifacts": {},
    }
    return _streaming_response(_run_stream(graph_input, req.session_id))


@router.post("/resume")
def chat_resume(req: ResumeRequest) -> StreamingResponse:
    """恢复被 interrupt 挂起的一轮：用人工决定继续驱动同一条线程。"""
    snapshot = _state_of(req.session_id)
    if not snapshot.get("__interrupt__"):
        raise HTTPException(409, "该会话当前没有待人工确认的中断")
    return _streaming_response(_run_stream(Command(resume=req.decision), req.session_id))


def _state_of(session_id: str) -> dict:
    """读检查点里的图状态；线程不存在时返回空 dict（不抛错，前端首次加载即可用）。"""
    try:
        snapshot = app_graph.get_state(_thread_config(session_id))
    except Exception:  # noqa: BLE001 检查点不可用不应让接口挂掉
        return {}
    values = dict(snapshot.values or {})
    # __interrupt__ 挂在快照上而不是 values 里，需要显式并入
    if snapshot.next:
        values["next"] = list(snapshot.next)
    for task in snapshot.tasks:
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            values["__interrupt__"] = interrupts
            break
    return values


@router.get("/history/{session_id}")
def get_history(session_id: str):
    """会话历史直接来自检查点（messages 通道），不再依赖进程内存储。"""
    state = _state_of(session_id)
    return {"messages": state.get("messages") or []}


@router.get("/state/{session_id}")
def get_state(session_id: str):
    """只读地暴露图状态的关键字段：排障、以及"刷新后恢复上次的分析结果"。

    带上 interrupt 载荷（而不只是一个 bool），前端刷新页面后仍能把待审核卡片还原出来——
    这正是"中断归属于 thread_id、状态存在检查点里"带来的好处。
    """
    state = _state_of(session_id)
    interrupts = state.get("__interrupt__")
    return {
        "intent": state.get("intent", ""),
        "artifacts": state.get("artifacts") or {},
        "pending_interrupt": bool(interrupts),
        "interrupt": _interrupt_payload(interrupts) if interrupts else None,
        "message_count": len(state.get("messages") or []),
    }


@router.post("/interview/end/{session_id}")
def end_interview(session_id: str):
    """结束面试：清挂起题目并留存本次评分记录（供导出报告）。"""
    get_interviewer(session_id).end_interview()
    return {"ok": True}
