# JobPilot 前端镜像（nginx 形态）。
#
# 两阶段：node 构建出 dist → 换成 nginx 镜像托管 dist 并反向代理 /api。
#
# 这里刻意【不】注入 VITE_API_BASE：nginx 形态下前端就该走 '/api' 前缀，
# 正好是 api.ts 的默认值，所以 dev（vite 代理）与生产（nginx 反代）行为一致。
# 对比单容器版必须注入 VITE_API_BASE=""（那是同源根路径的走法）。
#
# 构建上下文必须是项目根目录（要拷 deploy/nginx.conf）：
#   docker build -f frontend/Dockerfile -t jobpilot-web .

# ---------- stage 1：构建前端 ----------
FROM node:22-slim AS build

WORKDIR /build

# 先只拷 lock 文件命中依赖层缓存：改业务代码不会触发 npm ci 重跑
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------- stage 2：nginx 托管 ----------
FROM nginx:1.27-alpine

# 覆盖官方默认站点配置：默认那份只有静态托管，没有 /api 反代，也没有 SSE 相关处理
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

# 只探静态首页：它不依赖后端，所以 nginx 能立刻变 healthy。
# 后端是否就绪由 compose 的 depends_on.service_healthy 单独保证。
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
