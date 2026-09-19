"""聊天接口：SSE 真流式输出。

链路：节点内 LLM stream=True -> langgraph custom stream -> 这里逐事件转发 SSE。
面试考点：为什么用 SSE 而不是 WebSocket？
-> 单向推送场景下 SSE 更轻：走 HTTP、自动重连、代理友好，无需额外协议升级。
"""
import json
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.graph import app_graph
from app.schemas import ChatMessage

router = APIRouter(prefix="/chat", tags=["chat"])

# 简单的会话记忆（骨架阶段进程内存储；TODO: 换 Redis / SQLite）
_sessions: dict[str, list[ChatMessage]] = {}

Mode = Literal["auto", "jd_analysis", "resume_advice", "mock_interview", "general_chat"]


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str
    mode: Mode = "auto"  # auto=Planner 关键词路由；显式指定则跳过路由（前端模式选择器）


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    history = _sessions.setdefault(req.session_id, [])
    history.append(ChatMessage(role="user", content=req.message))

    def generate():
        try:
            # stream_mode 双通道：
            #   custom  -> 节点内部推送的 {"delta"|"status": ...}，实时转发
            #   values  -> 每个节点结束后的全量 state（最后一个即最终态），取 intent/artifacts
            reply_parts: list[str] = []
            final_state: dict = {}
            graph_input = {
                "messages": history,
                "user_input": req.message,
                "session_id": req.session_id,
                "force_intent": req.mode,
                "intent": "",
                "reply": "",
                "artifacts": {},
            }
            for mode, chunk in app_graph.stream(graph_input, stream_mode=["custom", "values"]):
                if mode == "custom":
                    if "delta" in chunk:
                        reply_parts.append(chunk["delta"])
                        yield _sse("delta", {"text": chunk["delta"]})
                    elif "status" in chunk:
                        yield _sse("status", {"text": chunk["status"]})
                else:  # values
                    final_state = chunk

            reply = "".join(reply_parts) or final_state.get("reply", "") or "（空回复）"
            history.append(ChatMessage(role="assistant", content=reply))
            if final_state.get("artifacts"):
                yield _sse("artifacts", final_state["artifacts"])
            yield _sse("done", {"intent": final_state.get("intent", "")})
        except Exception as exc:  # noqa: BLE001
            # 带上异常类型名：只发 str(exc) 时 KeyError 这类错误只剩一段引号文字，没法定位
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        # 禁用代理/浏览器缓冲，确保增量逐个到达前端
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history/{session_id}")
def get_history(session_id: str):
    return {"messages": [m.model_dump() for m in _sessions.get(session_id, [])]}
