"""FastAPI 入口。启动：uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes_chat import router as chat_router
from app.api.routes_kb import router as kb_router
from app.config import get_settings
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
    # 白名单来自配置（默认只放开发地址）。生产是单容器同源部署，
    # 浏览器不发跨域请求，所以这里不需要也不应该全开 "*"。
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(kb_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "warmup": getattr(app.state, "warmup", None)}


# 前端构建产物：单容器形态（Dockerfile）的多阶段构建会把它拷到 frontend/dist，
# 由这里挂载成同源静态站点；nginx 形态（docker compose）下后端镜像不拷 dist,
# 静态资源由 nginx 托管，这段检测不到目录会自动跳过——同一个后端镜像
# 在两种部署形态下都不用改代码。
# 必须放在所有路由注册之后：Starlette 按注册顺序匹配，挂在 "/" 的 StaticFiles
# 会兜住一切未命中的路径，先注册的 /chat、/kb、/health 优先级更高。
# 目录不存在（纯后端开发场景）时静默跳过，前端仍旧用 vite dev server。
_dist_dir = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="frontend")
    logging.getLogger(__name__).info("已挂载前端静态资源：%s", _dist_dir)
