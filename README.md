# JobPilot · 智能求职助手 Agent

[![CI](https://github.com/wdfwdf666/jobpilot-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/wdfwdf666/jobpilot-agent/actions/workflows/ci.yml)

基于 **LangGraph 多 Agent 编排 + RAG + Function Calling** 的求职助手：
把 JD 丢进来，它会做 JD 深度分析 → 简历匹配与优化建议（带原文引用）→ 基于个人知识库的模拟面试。

前端 Vue 3 + TypeScript，后端 FastAPI + SSE 真流式输出。
JD 分析在图上挂了一层**人工审核**（LangGraph `interrupt`）——要求抽取错了可以当场改，改完重算匹配度。

> 进度：**D1-11 已完成** —— 项目骨架 / 简历解析入库 / 检索与 LLM 打通 / 知识库可视化管理 /
> JD 分析（结构化抽取 + 简历逐条匹配打分，带原文证据）/ 模拟面试（出题 → 批改 → 追问状态机）/
> **混合检索（BM25 + 向量 + RRF）与评测体系**（检索 recall/MRR 对比 + LLM-as-judge 回答质量评分）。
> **D12 已完成**：Docker 部署双形态 —— nginx 双容器（compose 编排 + 反向代理 + SSE 直通）
> 与单容器（多阶段构建 + 健康检查 + 非 root 数据卷）。
> **D12 增补**（三个能力一起把 Agent 闭环补上）：
> ① **Function Calling** —— 模型自主决定是否联网，工具循环 + 步数上限 + 失败降级（`app/tools/tool_loop.py`）；
> ② **人机协同** —— SqliteSaver 检查点 + `interrupt` 人工审核 + `Command(resume=...)` 续跑；
> ③ **结果有出口** —— 匹配度可视化面板（结构化产物直接渲染）与面试评分报告导出（回链知识库薄弱板块）；
> ④ **会话可回到过去** —— 面试评分与会话索引落盘（与检查点同库同连接），左侧会话列表支持切换恢复与删除。
> 下一步 D13-14：demo 录屏。

## 架构

```
Vue 3 + TS (Vite, :5173)
        │  POST /chat/stream        (SSE: delta / status / artifacts / interrupt / done)
        │  POST /chat/resume        (SSE，带上人工审核决定续跑同一线程)
        ▼
FastAPI (:8000)
        │
   LangGraph Supervisor  ── Planner 意图路由
        ├── JD 分析师      （JD 拆解 → 结构化抽取 + 逐条匹配打分）
        │        └── JD 人工审核  ← interrupt：可确认 / 修正要求后重算
        ├── 简历顾问      （匹配度分析 + 改写建议，引用简历原文）
        ├── 面试官        （基于个人知识库出题 + 追问 + 评分留档）
        ├── 通用对话      （自由问答，按需自主调用联网搜索）
        └── finalize      （统一把回复落进对话历史）
                    │
              RAG 检索层（Qwen text-embedding-v4 + ChromaDB，category 作用域过滤）
                    │
              工具层（web_search · 博查 BochaAI，Function Calling 按需触发）
                    │
              SqliteSaver 检查点（图状态 + 对话历史，按 thread_id 持久化）
                    │
              业务存储（面试评分记录 + 会话索引，与检查点同库同连接）
```

## 接口一览

前端只跟一个同源基址打交道（dev 由 vite 代理 `/api` 到 8000，生产由 nginx 反代，路由前缀始终不变）。

**对话 `/chat`**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat/stream` | SSE 主入口。事件类型：`status` / `delta` / `artifacts` / `interrupt` / `done` / `error` |
| POST | `/chat/resume` | 带上人工审核决定续跑**同一 thread**（`Command(resume=...)`），帧格式与 `/stream` 共用一份实现 |
| GET | `/chat/history/{session_id}` | 对话历史。直接读检查点的 `messages` 通道，不依赖进程内状态，所以重启后端也接得上 |
| GET | `/chat/state/{session_id}` | 会话状态，含 `pending_interrupt`——刷新页面后据此把待审核卡片还原出来 |
| POST | `/chat/interview/end/{session_id}` | 结束面试并**保留**逐题记录（报告是"结束之后"才导出的） |
| GET | `/chat/sessions` | 左侧会话列表（最近 50 个，按最近活跃倒序）。与检查点里出现过的 thread 取并集，历史线程不凭空消失 |
| DELETE | `/chat/sessions/{session_id}` | 删除会话：索引行 + 面试记录 + 检查点线程三处一起清，缺一处就会"复活" |

**面试报告 `/interview`**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/interview/report/{session_id}` | 聚合逐题记录成 Markdown，外加 `knowledge_gaps`（薄弱点 + 回链到的知识库文档） |
| GET | `/interview/records/{session_id}` | 只取结构化逐题记录（题目 / 回答 / 得分 / 对错要点 / 追问） |

**知识库 `/kb`**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/kb/documents` | 粘贴文本入库；`category=简历素材` 时自动走简历板块解析器 |
| POST | `/kb/upload` · `/kb/upload-batch` | 单文件 / 批量上传（md / txt / pdf / docx / html），批量逐文件返回结果 |
| POST | `/kb/search` | 检索测试，返回命中块与分数，用于调参时肉眼核对 |
| GET | `/kb/stats` · `/kb/documents` · `/kb/chunks` | 库统计 / 文档列表（含块级标签）/ 块列表 |
| PUT | `/kb/chunks/{chunk_id}` | 改块文本并**重新向量化**（删旧 + 按新内容入库，复用同一套幂等去重） |
| DELETE | `/kb/documents` · `/kb/chunks/{chunk_id}` | 删文档 / 删块 |

`GET /health` 返回 warmup 报告（向量库 / embedding / LLM / retriever 是否就绪 + 块数），Docker 的健康检查打的就是它。

## 项目亮点

- **多 Agent 显式编排**：用 LangGraph `StateGraph` 而不是隐式 Agent 链 —— 状态可枚举、路径可回溯、节点可单测。
- **人在环（human-in-the-loop）**：JD 分析后图会 `interrupt` 停住，把抽取出的硬性/加分要求交给用户确认；用户也可以改完再继续，此时匹配度按修正后的要求**重算**。恢复靠 `Command(resume=...)` 打同一 `thread_id`，所以刷新页面、重启进程都接得上（`SqliteSaver` 落盘）。SSE 协议上把中断翻译成一个 `interrupt` 事件且**不发 `done`**，前端据此渲染审核卡片。
- **结构化产物直接可视化**：JD 匹配不走"让模型把分数写进 markdown"，而是结构化输出经 SSE `artifacts` 推到前端，渲染成匹配度面板（总分环形 + 逐条要求分数条 + 简历原文证据 + 补强建议），最弱的排最前面。
- **结果有出口**：模拟面试逐题留档，结束后聚合成一份 Markdown 评分报告（总分 / 逐题表 / 逐题明细），并对每个薄弱点回链到知识库——给出可检索关键词与该板块里已有的相关材料，前端 chip 一点就直接跳过去搜。
- **会话持久化到盘，不止到内存**：图状态与对话历史由 `SqliteSaver` 落 SQLite；面试的逐题评分与面试官上下文落**同一个库**（`app/storage.py`，复用 checkpointer 的连接，避免同文件双连接的 `database is locked`）。前端左侧有会话列表：标题取首条消息、面试会话带徽标，可切换恢复、可删除（索引 + 面试记录 + 检查点线程三处一起清）。内存里的面试官注册表退化为**纯缓存**——LRU 淘汰只影响下次要重新 load，不再丢数据。明确边界：单进程（uvicorn 单 worker）为前提，多实例需把共享状态外置。
- **可追溯的回答**：所有基于知识库的结论都带原文片段与来源文件，通过 SSE `artifacts` 事件推到前端渲染，不是"黑盒胡说"。
- **LLM 自主调用工具**：联网搜索以 Function Calling 形式提供，由模型自己判断该不该搜——问"某公司做什么"它会搜，问"RAG 和微调的区别"它不搜。工具循环留在节点内部而不摊进图（图仍是一进一出），用步数上限防死循环，工具报错则把错误原文回喂让模型降级作答。
- **简历按板块解析入库**：简历不是普通文档。解析器识别（基本信息/教育/技能/工作/项目/荣誉/自我评价）分段，并给每段打板块标签，检索时按类别缩小范围，精度显著优于整篇滑窗切分。
- **知识库可运营**：粘贴入库 / 文件上传 / 检索测试 / 文档管理（查看块、改文本重新向量化、删块、删文档），不是只能进不能出的 demo。
- **真流式**：LangGraph `get_stream_writer()` 自定义流 → SSE 增量推送 token（不是打完后一次性吐出来）。
- **并发安全**：进程级单例 + 启动预热，修掉了 ChromaDB "Could not connect to tenant" 与 SDK 惰性导入导致的并发冷启动 500（见下方"工程难点"）。

## 快速开始

```bash
# 1. 创建虚拟环境并安装（国内建议加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple）
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 2. 配置密钥（阿里云百炼，一份 Key 同时用于 LLM 和 Embedding）
copy .env.example .env   # 填入百炼 API Key：https://bailian.console.aliyun.com/
python scripts/check_llm.py    # 连通性自检（含结构化输出、分批 embedding）

# 3. 导入知识（支持 md / txt / pdf / docx / html）
python scripts/ingest.py path/to/notes.md --category 八股文

# 3b. 导入简历（自动按板块解析，检索更精准）
python scripts/ingest_resume.py "path/to/简历.pdf"
python scripts/ingest_resume.py "path/to/简历.pdf" --replace   # 改版重传：清掉同源旧块
python scripts/ingest_resume.py "path/to/简历.docx" --query "简历里的项目经历有什么可优化的"

# 4. 启动后端（先起后端再起前端）
uvicorn app.main:app --reload
# 启动日志会打印 warmup 报告：{"vector_store":"ok (29 chunks)", ...}

# 5. 启动前端（新终端，5173，/api 代理到 8000）
cd frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5173 ，左列上方是**会话列表**（新建 / 切换恢复 / 删除），下方是知识库面板（粘贴入库 / 上传文件 / 文档管理 / 检索测试），右侧是对话区。

## Docker 部署

提供两种形态，二选一。

### 形态 A：nginx + 后端双容器（推荐，生产形态）

```bash
docker compose up -d --build
# 打开 http://localhost:8080  —— nginx 托管前端，并把 /api 反向代理到后端
docker compose logs -f api     # 后端 warmup 报告在这里
docker compose down            # 停止（数据卷保留）
```

- `web`：`nginx:alpine`，托管 `dist` 静态资源 + 反代 `/api` → `api:8000`
- `api`：FastAPI，**不写 `ports`，即不暴露到宿主机**，只在 compose 内部网络可达

换对外端口：`APP_PORT=9000 docker compose up -d`，或写进 `.env`。

### 形态 B：单容器（轻量，适合只给一个端口的 PaaS）

```bash
docker build -t jobpilot .
docker run -p 8001:8000 --env-file .env -v jobpilot_data:/app/data jobpilot
# 打开 http://localhost:8001  —— 前端由 FastAPI 同源托管，不需要 nginx
```

> 宿主机端口刻意用 8001：容器内统一 8000（镜像可移植），宿主机端口让开本地开发用的
> `uvicorn --reload`，避免抢端口。想用 8000 就把 `-p 8000:8000` 换回去，
> 但记得先停掉本地 dev server。

### 两种形态的差别

| | 形态 A（nginx） | 形态 B（单容器） |
|---|---|---|
| 前端托管 | nginx 直出（gzip + 分层缓存） | FastAPI `StaticFiles` |
| 后端暴露 | 不暴露，只能经 nginx | 与前端同一端口 |
| 容器数量 | 2（nginx 常驻约 20 MB） | 1 |
| 前端构建参数 | 默认 `/api`（由 nginx 反代） | 注入 `VITE_API_BASE=""`（同源根路径） |
| 适用场景 | VPS / compose 环境 | 单端口 PaaS、本地快速验证 |

两套共用同一份前端源码，差别只在**构建期注入的环境变量**：
`frontend/src/api.ts` 里 `BASE = import.meta.env.VITE_API_BASE ?? '/api'`，
走 `/api` 前缀还是走同源根路径，由构建参数决定，后端路由前缀始终不变。

### 密钥与数据（两种形态一致）

- **多阶段构建**：前端在 `node:22-slim` 阶段构建，运行镜像只带结果不带工具链。
- **密钥不进镜像**：`.env` 被 `.dockerignore` 排除，只在运行时用 `--env-file` 注入。
  镜像里也没有向量库和上传文件——`data/` 整个被排除，`data/uploads` 里是真实简历，
  打进镜像等于把个人信息推到镜像仓库。
- **持久化**：容器内 `data/` 是挂载点，用 volume 保留向量库。不挂的话每次重建容器都要重新入库。
- **非 root 运行**：镜像内建 `appuser`（uid 10001）并 `chown` 数据目录。
  注意 `chown` 必须在 `USER` 之前——命名卷首次创建时继承挂载点属主，顺序反了运行期写向量库会 PermissionError。
- **健康检查**：后端 `HEALTHCHECK` 打 `/health`（返回 warmup 报告，能直接看出向量库起没起），
  `start-period` 给到 40s（chroma 首次初始化 + embedding 预热）。compose 里 `web` 用
  `depends_on: condition: service_healthy` 等它就绪，避免首屏吃 502。

### nginx 配置里三个必须写对的点（`deploy/nginx.conf`）

1. **`proxy_pass` 的尾斜杠不能省**：`proxy_pass http://api:8000/;` 结尾这个 `/` 会把 `/api/`
   前缀替换成 `/`，于是 `/api/chat/stream` 到后端就是 `/chat/stream`；漏掉斜杠会原样透传前缀，
   后端全部 404。语义与 `vite.config.ts` 里的 `rewrite: path.replace(/^\/api/, '')` 一致。
2. **SSE 必须关 `proxy_buffering`**：默认开着的话流式回答会被 nginx 攒在缓冲区里，
   前端表现为"卡住几秒然后整段弹出"，逐字效果全没了。后端已带 `X-Accel-Buffering: no`
   响应头，nginx 侧再显式关一次做双保险；同时把 `proxy_read_timeout` 从默认 60s 放宽到 300s。
3. **`client_max_body_size`**：默认只放 1m，上传简历 PDF 会在 nginx 入口直接 413，
   请求根本到不了后端，很容易被误判成后端上传逻辑的 bug。

另外 `index.html` 必须 `no-cache`（它记录着当前版本引用的 chunk 文件名，被缓存住就会拿着旧 html
去请求已删除的旧 chunk → 白屏），而 `/assets/` 下的 hash 产物可以 `immutable` 长缓存。

容器内也能跑自检脚本排障：

```bash
# 形态 A：在运行中的 api 容器里执行
docker compose exec api python scripts/check_llm.py
docker compose exec api python scripts/check_rag.py
# 形态 B
docker run --rm --env-file .env jobpilot python scripts/check_llm.py
```

## 技术选型

| 能力 | 选型 | 说明 |
|------|------|------|
| LLM | `qwen-plus`（百炼） | 1M 上下文、¥0.8/百万输入 token，能力与成本均衡 |
| Embedding | `text-embedding-v4`（Qwen3-Embedding） | 1024 维可调、¥0.07/百万 token；单批上限 10 条，代码已自动分批 |
| 向量库 | ChromaDB（cosine） | 轻量零运维，后续可换 Milvus / pgvector |
| 编排 | LangGraph | 显式状态图，比隐式 Agent 链可控可回溯 |
| 后端 | FastAPI + SSE | 流式输出用 SSE 而非 WebSocket：单向推送、天然过代理 |
| 前端 | Vue 3 + TS + Vite | marked + DOMPurify + highlight.js 渲染 markdown，SSE 用 fetch + ReadableStream |

> 换模型只需改 `.env`，业务代码零改动。

## 模块说明

| 路径 | 职责 |
|------|------|
| `app/config.py` | 全局配置（pydantic-settings，读 .env） |
| `app/rag/` | 文档解析（pdf/docx/html）→ 简历板块解析 → 分块 → 向量化 → 检索 |
| `app/agents/` | Planner + 四个子 Agent + LangGraph 编排（真流式，检索按类别注入上下文，JD 分析后挂人工审核中断） |
| `app/agents/checkpoint.py` | SqliteSaver 进程级单例：图状态与对话历史落盘，`interrupt` / `resume` 的前提 |
| `app/tools/` | 联网搜索（博查 BochaAI）+ Function Calling 工具循环：模型自主决定是否联网，带步数上限与失败降级 |
| `app/api/` | REST + SSE 流式接口（含中断恢复 `/chat/resume`）；知识库管理接口（`category=简历素材` 自动走简历解析器） |
| `app/api/routes_interview.py` | 面试评分报告接口：聚合逐题记录成 Markdown + 知识库回链 |
| `app/warmup.py` | 启动预热：把重客户端初始化提前到单线程阶段，消除并发冷启动竞态 |
| `frontend/` | Vue 3 对话界面：流式回答、markdown 渲染、引用来源可展开、**匹配度面板**、**人工审核卡片**、面试报告导出、知识库管理面板 |
| `app/main.py` | FastAPI 入口 + 前端静态托管挂载（检测到 `frontend/dist` 自动生效，纯后端开发时静默跳过） |
| `docker-compose.yml` + `deploy/nginx.conf` | nginx 形态编排：静态托管 + `/api` 反代（SSE 直通、上传体积、缓存分层） |
| `Dockerfile.backend` / `frontend/Dockerfile` | nginx 形态的两个镜像：纯后端（无 node、无 dist）+ nginx 前端 |
| `Dockerfile` / `.dockerignore` | 单容器形态多阶段构建；构建上下文排除 `.env`、`data/`（含真实简历）与本地调试脚本 |
| `scripts/eval_retrieval.py` | 检索评测：recall@k / MRR + 逐条诊断 + 语料一致性自检 |
| `scripts/eval_answer.py` | LLM-as-judge：忠实度 / 相关性打分 + 基线对比 |
| `scripts/` | 入库 CLI（知识/简历）+ 各层自检脚本（LLM / RAG / SSE / Agent / 并发 / 工具调用） |

## 测试与自检

```bash
pytest -q                      # 单元测试（解析器 / 配置 / 单例 / 混合检索 / Prompt 回归 / 工具循环 / 人审中断 / 面试报告）
python scripts/check_rag.py        # RAG 链路：入库 -> 检索
python scripts/check_stream.py     # SSE 真流式 + 引用来源（走 HTTP）
python scripts/check_agent.py      # Agent 端到端：路由 + 检索注入 + 流式 + 来源 + 人审中断与自动 resume
python scripts/check_agent.py "分析这个 JD：…" --hold   # 停在人工审核处，只打印中断载荷
python scripts/check_tools.py      # Function Calling：模型是否自主调用、该不用时是否克制
python scripts/check_concurrent.py --mix   # 并发安全：混合端点压测
python scripts/eval_retrieval.py --check-corpus   # 【改库后先跑】用例是否已过期
python scripts/eval_retrieval.py -v               # 检索评测 + 逐条诊断
python scripts/eval_answer.py -v                  # LLM-as-judge + 依据/回答明细
```

人机协同相关的一次性验证（`tests/test_graph_review.py`，用内存检查点 + 假 LLM，不烧额度）覆盖：
中断发生在出正文之前、`approve` 续跑并落历史、`edit` 按修正要求重算、thread 之间历史隔离、
以及关掉 `HUMAN_REVIEW_JD` 后退化为全自动。

**CI**（`.github/workflows/ci.yml`）：push 到 `main` 或开 PR 时并行跑两个 job —— 后端 `pytest`、
前端 `vue-tsc + vite build`，不需要任何密钥（测试本身是 hermetic 的）。
它抓的第一件事就很典型：11 条用例只在"本地有 `.env`"时通过，干净环境里直接抛
`Embedding 无可用 API Key`——根因是 `InterviewerAgent` 构造时 eager 建 Retriever，
现在改成首次检索才构造。想本地复现 CI 条件，一行就够：

```bash
LLM_API_KEY= EMBED_API_KEY= pytest -q     # 模拟没有 .env 的干净机器
```

## 评测：指标怎么看、怎么改

### 两个脚本分别量什么

| 指标 | 含义 | 回答什么问题 |
|---|---|---|
| `recall@k` | top-k 里有没有命中期望文档 | 该检索到的内容，检索到了吗 |
| `MRR` | 命中名次的倒数均值 | 命中排得够不够前（第 1 位比第 5 位值钱得多） |
| `faithfulness` | 回答是否只依据检索片段（1-5） | RAG 有没有压住幻觉 |
| `relevance` | 回答是否切题（1-5） | 检索到的料够不够回答问题 |

### 怎么知道"具体是哪条挂了"

`-v` 逐条打印期望条件、两种模式的名次、MISS 时展开实际 top5 并给出归因：

```
[11/14] 候选人的技术栈有哪些  (#cv-techstack)
    期望：类别=「简历素材」 + 内容含 ['FastAPI'] 之一
    vector : ✓ rank=3  王东风.pdf[专业技能] | 0.471 | 【专业技能】Linux 熟悉LangGraph…
    hybrid : ✗ MISS  期望文档召回了，但含该内容的块没进 top-k → 召回失败
```

归因分三类，指向的修法完全不同：**来源不符** → 改元数据/用例；**文档召回但目标块没进** → 调检索（候选池大小、融合策略）；**整个文档没进 top-k** → 换检索模式或确认库里有没有。

### 尺子本身可能是坏的（真实踩坑）

第一版 `is_hit` 写成"来源匹配 **或** 关键词子串命中"，跑出来两种模式 recall@1/3/5 **全 100%**、MRR 也 100%——指标看着完美，实际毫无分辨力，因为：

- 子串匹配让 `rag` 命中了 `Average Pooling`（"ave**rag**e"）；
- 来源匹配让"文档被召回但内容完全不相关"也算命中；
- 库里删掉文档后，用例变成"永久命中"的假通过。

修法：命中判定改为**来源/类别匹配 AND 关键词真正出现在该块文本里**（拉丁词按词边界匹配，避免子串误伤），并新增 `--check-corpus` 在跑评测前自检用例是否还和库对得上。改口径后同一份代码的指标从"全 100%"变成 `recall@1 85.7% / MRR 90.5%`——**先修尺子，再谈调优**。

### 改了什么、怎么确认有没有变好

```bash
python scripts/eval_retrieval.py --save _eval/base.json       # 留一份基线
# ...改配置 / 改分块 / 换模型...
python scripts/eval_retrieval.py --save _eval/new.json --baseline _eval/base.json
# 输出：每项指标涨跌 + 修好/弄坏的用例清单 + 名次变动
```

可调的旋钮（都走 `.env`，改完就跑评测，别拍脑袋）：

| 想提升 | 动什么 | 位置 |
|---|---|---|
| 检索召回 | `RETRIEVAL_MODE`（hybrid / vector） | `.env` |
| 融合策略 | `RRF_K`、`RECALL_PER_CHANNEL` | `.env` |
| 切块质量 | `CHUNK_SIZE`、`CHUNK_OVERLAP`（需重新入库） | `.env` |
| 简历检索精度 | 上传 md/docx 而非 PDF（PDF 抽取会打乱板块边界） | 知识库面板 |
| 回答忠实度 | 分块粒度、system prompt 约束、检索 top_k | 代码 / `.env` |

### 当前结论（如实记录，含不利结果）

25 块小语料、14 条用例下：`recall@3/@5` 两路接近饱和，**混合检索的 MRR 略低于纯向量（-1.2%）**——定位到唯一失分用例：问"候选人的技术栈有哪些"，向量能在第 3 位找回含 `FastAPI` 的块，混合检索的 top5 里它被同文档其他近似块挤掉了（RRF 会奖励"两路都命中"的块）。另外 `RRF_K` 在 10~200 之间对结果无影响，说明该参数在本库不敏感、不必调。

还有一条必须说清楚的：**14 条样本下 1 条 = 7.1%，±14% 以内的差异（≤2 条用例）是噪声**，不足以支撑"混合检索更好/更差"的结论。所以下一步不是继续调参，而是先把评测集扩到 30~50 条，再让数字说话。

`eval_answer.py` 则用裁判模型给回答打"忠实度/相关性"分——首轮评测即抓到一处真实幻觉（回答引入了检索依据中不存在的"平移不变性"），证明该机制能定位"RAG 没压住幻觉"的具体环节。定位口径：**忠实度高+相关性低 → 检索没给对料；忠实度低 → 检索不足或模型硬编；两者都高但读着不对 → 先怀疑评测集和裁判 prompt**。

## 工程难点复盘

1. **"假流式"是怎么来的**：最初直接 `await llm.ainvoke()` 再一次性 yield 整个结果，前端看起来是"文本突然全部出现"。改成 LangGraph `get_stream_writer()` 自定义流（节点内 `writer({"text": delta})`），配合 `stream_mode=["custom","values"]` 才拿到 token 级增量。
2. **并发冷启动 500**：uvicorn 把同步端点丢进线程池，多请求真并行；`chromadb.PersistentClient(...)` 构造时会初始化 Rust bindings 并校验 tenant，首次构造发生在请求线程里就会偶发 `ValueError: Could not connect to tenant default_tenant`（单请求永远正常）。解法：向量库/Embedding/Retriever 全部进程级单例（双检锁）+ 启动预热。同类问题还有 openai SDK 子模块惰性导入引发的 `_ModuleLock` 死锁。
3. **增量入库与"修改"共用一套幂等逻辑**：`doc_id = sha1(source + text)`，重复入库天然跳过；改块的文本会得到新 id，所以"修改 = 删旧 + 按新内容入库"，同一套去重规则复用，不会产生重复块。
4. **Windows 上传踩坑**：`shutil.move` 移动仍被打开的文件会报 `WinError 32`，改成直接 `write_bytes` 落盘 + 文件名取 `Path(filename).name` 防路径穿越。
5. **块级标签失效**：入库时 `tags` 传的是"全文档板块并集"，导致简历每个块都带上了所有板块标签，"按板块过滤"形同虚设——检索诊断时发现（每个块的 tags 完全一样）。改为支持 `per_chunk_tags`，块只带自己所属板块；同时加了板块识别置信度告警（PDF 抽取把标题顺序打乱时，97% 正文会挤进单个板块，此时明确提示改用 md/docx）。
6. **隐式依赖只在干净环境暴露**：镜像构建后容器启动即崩，报 `Form data requires "python-multipart"`。
   它是 FastAPI 处理 `UploadFile` 的**运行时依赖**，代码里没人 `import multipart`，
   本地恰好装着所以毫无症状——**"扫 import"的排查手段根本抓不到它**。
   本地的依赖列表无法证明声明完整，只有干净环境装一次才能证伪；这正是容器化的价值。
7. **部署期的前端同源问题**：前端接口基址写死 `'/api'`，那是 vite dev 代理引入的约定，
   打包成静态文件后没有任何东西去接，接口全 404。改为构建期可注入
   （`VITE_API_BASE=""` 走同源根路径），dev 链路与后端路由前缀都不用动。
   注意必须用 `??` 而非 `||`——空字符串是合法取值，`||` 会把它吞掉导致注入静默失效。
8. **反向代理接 SSE 的两个坑**：切到 nginx 形态后流式失效的风险点有两处——
   一是 `proxy_buffering` 默认开启，会把 token 攒在缓冲区里（前端表现为"卡住几秒然后整段弹出"），
   这里靠后端 `X-Accel-Buffering: no` 响应头 + nginx 侧 `proxy_buffering off` 双保险；
   二是 `proxy_pass` 的尾斜杠语义（`http://api:8000/` 剥掉 `/api` 前缀 vs `http://api:8000` 原样透传），
   写错就是全站 404，而且报错信息完全指不到原因。这类"配置项语义差异"只能靠实测验证。
9. **静态资源缓存分层**：hash 产物（`/assets/`）可以 `immutable` 缓存 30 天，
   但 `index.html` 必须 `no-cache`——它记录着当前版本引用的 chunk 文件名，
   一旦被缓存住，用户会拿着旧 html 去请求已经删掉的旧 chunk，直接白屏。
10. **interrupt 不是异常，是"存下状态再抛"**：第一次接人机协同时很容易以为
    "加个 `interrupt()` 就完事"，实际是三件套：图必须带 checkpointer 编译、
   调用必须传 `thread_id`、恢复必须用 `Command(resume=...)`。少任何一件，
   中断都无处可存或无处可续。另外 SSE 侧要显式区分中断与正常结束——
   中断时**不能发 `done`**，否则前端以为这一轮跑完了，审核卡片永远不出现。
11. **节点内 `interrupt()` 之后的代码才是"只执行一次"的**：恢复时整个节点函数会从头再执行，
   这次 `interrupt()` 直接返回人工输入。所以 LLM 调用、流式输出这类重活必须放在
   `interrupt()` **之后**，否则每次恢复都会把前面的活再干一遍（重复计费 + 重复副作用）。
   与本项目相关的推论：JD 分析被拆成 `jd_analyst`（结构化抽取，不出文案）与
   `jd_review`（审核 + 出文案）两个节点，既避免恢复时重跑抽取，也保证人工修正后
   "结论只出一版"。
12. **持久化图状态会让"上一轮的回复"残留**：带 checkpointer 后 `state["reply"]`
   跨轮存在，若直接用 `final_state["reply"]` 兜底，中断那一轮会把上一轮的回复当成本轮结果。
   解法是优先用本轮实际流出的 delta 拼接，并且在中断时直接返回、不落到兜底分支。
13. **检查点里存 dict 比存 pydantic 模型稳**：`messages` 用 `list[dict]` 而不是
   `ChatMessage` 模型，序列化器对 dict / 原生类型最稳，跨版本升级也不容易踩类型坑。
14. **并行编辑同一文件会互相覆盖**：本轮实现中我两次并行对同一文件发起编辑，
   后写的那次把先写的冲掉了（`config.py` 的字段定义、`main.py` 的 import 各丢一次），
   靠事后 grep 复查才发现。**改完必须回读文件确认内容**，不能只看工具返回的 success。
15. **分批迭代会留下"持久化水位不一致"的缝**：`/chat` 的历史先换成了检查点落盘，
   而面试评分还留在进程内存 + 50 场静默淘汰——用户重启一次，报告就无声没了。
   补法不是"再写一个存储"，而是**把业务记录落进检查点同一个库、复用同一条连接**
   （同文件双连接会撞 `database is locked`），并把内存注册表的语义降级为缓存：
   淘汰只影响下次要重新 load，数据本身在盘上。

## License

MIT
