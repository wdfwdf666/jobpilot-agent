# JobPilot 单容器镜像：多阶段构建
#
# 为什么用多阶段？
#   1. 前端需要 node 工具链、后端只需要 python，两者塞一个镜像会白白多几百 MB；
#   2. 构建产物（frontend/dist）留在 stage1，运行阶段只拷结果，不带 node_modules。
#
# 为什么是单容器而不是 backend + nginx 双容器？
#   本项目前端是无路由的对话 SPA，静态托管需求极简，让 FastAPI 顺带托管
#   （见 app/main.py 的 StaticFiles 挂载）可以把「同源 + 免跨域 + 单端口」
#   一次性解决，省掉 nginx 配置与额外的容器编排。规模上来后再拆不迟。
#
# 构建：docker build -t jobpilot .
# 运行：docker run -p 8000:8000 --env-file .env -v jobpilot_data:/app/data jobpilot

# ---------- stage 1：构建前端 ----------
FROM node:22-slim AS frontend

WORKDIR /build

# 先只拷 lock 文件命中依赖层缓存：改业务代码不会触发 npm ci 重跑
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# 同源部署的关键开关：空字符串表示前端直接用根路径调后端。
# 不注入时 api.ts 回落到 '/api'（那是给 vite dev 代理用的前缀）。
ENV VITE_API_BASE=""
RUN npm run build


# ---------- stage 2：运行时 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依赖层单独缓存：只改业务代码时不会重装 chromadb / pymupdf 这些大包。
# --mount=type=cache 让 pip 的下载缓存跨构建复用（缓存挂载不进镜像层，
# 所以不会让镜像变大）；因此这里刻意不设 PIP_NO_CACHE_DIR。
COPY pyproject.toml ./
COPY app/ ./app/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

# 自检脚本一并带上：容器里也能跑 check_llm / check_rag，排障不用另起环境
COPY scripts/ ./scripts/

# 前端产物由 stage1 拷入，FastAPI 启动时若检测到该目录会自动挂载
COPY --from=frontend /build/dist ./frontend/dist

# 非 root 运行：容器被突破时影响面小得多，属于部署基线要求。
# 顺序有坑——必须先建目录、先 chown、再切用户：
# 命名卷首次创建时会继承镜像里挂载点的属主，若先切用户再 mkdir，
# 卷会是 root 属主，运行期写向量库直接 PermissionError。
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p data/chroma data/uploads \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 复用已有的 /health（它会带上 warmup 报告，能直接看出向量库有没有起来）。
# start-period 给足 40s：chroma 首次初始化 + embedding 客户端预热都在启动阶段。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
