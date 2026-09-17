"""FastAPI 入口。启动：uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_kb import router as kb_router
from app.warmup import warmup

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动预热：在主线程串行完成惰性 import 与重客户端初始化。
    # 不做这一步的话，这些初始化会落在首个请求的线程池线程里，并发时互相竞争
    # （模块锁死锁 / chroma tenant 校验失败），症状是"单请求正常、一并发偶发 500"。
    app.state.warmup = warmup()
    yield


app = FastAPI(
    title="JobPilot",
    version="0.1.0",
    description="智能求职助手 Agent",
    lifespan=lifespan,
)

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
    return {"status": "ok", "warmup": getattr(app.state, "warmup", None)}
