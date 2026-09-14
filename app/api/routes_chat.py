"""聊天接口：SSE 流式输出。

面试考点：为什么用 SSE 而不是 WebSocket？
-> 单向推送场景下 SSE 更轻：走 HTTP、自动重连、代理友好，无需额外协议升级。
"""
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.graph import app_graph
from app.schemas import ChatMessage

router = APIRouter(prefix="/chat", tags=["chat"])

# 简单的会话记忆（骨架阶段进程内存储；TODO: 换 Redis / SQLite）
_sessions: dict[str, list[ChatMessage]] = {}


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    history = _sessions.setdefault(req.session_id, [])
    history.append(ChatMessage(role="user", content=req.message))

    def generate():
        try:
            # 骨架阶段：先整体跑图再伪流式吐出；TODO(P1): 各节点内部接 llm.chat_stream 真流式
            result = app_graph.invoke({
                "messages": history,
                "user_input": req.message,
                "intent": "",
                "reply": "",
                "artifacts": {},
            })
            reply = result.get("reply", "（空回复）")
            # 伪流式：按行推送，前端体验一致
            for line in reply.splitlines(True):
                yield _sse("delta", {"text": line})
            history.append(ChatMessage(role="assistant", content=reply))
            if result.get("artifacts"):
                yield _sse("artifacts", result["artifacts"])
            yield _sse("done", {"intent": result.get("intent", "")})
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/history/{session_id}")
def get_history(session_id: str):
    return {"messages": [m.model_dump() for m in _sessions.get(session_id, [])]}
