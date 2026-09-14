"""FastAPI 入口。启动：uvicorn app.main:app --reload"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_kb import router as kb_router

app = FastAPI(title="JobPilot", version="0.1.0", description="智能求职助手 Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 骨架阶段全开；TODO(部署): 收敛到前端域名
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(kb_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
