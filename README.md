# JobPilot · 智能求职助手 Agent

基于 **LangGraph 多 Agent 编排 + RAG + Function Calling** 的求职助手：
把 JD 丢进来，它会做 JD 深度分析 → 简历匹配与优化建议（带原文引用）→ 基于个人知识库的模拟面试。

前端 Vue 3 + TypeScript，后端 FastAPI + SSE 真流式输出。

> 进度：**D1-11 已完成** —— 项目骨架 / 简历解析入库 / 检索与 LLM 打通 / 知识库可视化管理 /
> JD 分析（结构化抽取 + 简历逐条匹配打分，带原文证据）/ 模拟面试（出题 → 批改 → 追问状态机）/
> **混合检索（BM25 + 向量 + RRF）与评测体系**（检索 recall/MRR 对比 + LLM-as-judge 回答质量评分）。
> 下一步 D12+：Docker 部署、demo 录屏；联网搜索（博查）已备好工具层，待接 Function Calling。

## 架构

```
Vue 3 + TS (Vite, :5173)
        │  POST /chat/stream  (SSE: delta / status / artifacts)
        ▼
FastAPI (:8000)
        │
   LangGraph Supervisor  ── Planner 意图路由
        ├── JD 分析师      （JD 拆解 → 硬性要求 / 加分项 / 关键词）
        ├── 简历顾问      （匹配度分析 + 改写建议，引用简历原文）
        └── 面试官        （基于个人知识库出题 + 追问）
                    │
              RAG 检索层（Qwen text-embedding-v4 + ChromaDB，category 作用域过滤）
```

## 项目亮点

- **多 Agent 显式编排**：用 LangGraph `StateGraph` 而不是隐式 Agent 链 —— 状态可枚举、路径可回溯、节点可单测。
- **可追溯的回答**：所有基于知识库的结论都带原文片段与来源文件，通过 SSE `artifacts` 事件推到前端渲染，不是"黑盒胡说"。
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

浏览器打开 http://localhost:5173 ，左侧是知识库面板（粘贴入库 / 上传文件 / 文档管理 / 检索测试），右侧是对话区。

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
| `app/agents/` | Planner + 三个子 Agent + LangGraph 编排（真流式，检索按类别注入上下文） |
| `app/api/` | REST + SSE 流式接口；知识库管理接口（`category=简历素材` 自动走简历解析器） |
| `app/warmup.py` | 启动预热：把重客户端初始化提前到单线程阶段，消除并发冷启动竞态 |
| `frontend/` | Vue 3 对话界面：流式回答、markdown 渲染、引用来源可展开、知识库管理面板 |
| `app/eval/` | LLM-as-judge 评测（第 2 周） |
| `scripts/` | 入库 CLI（知识/简历）+ 各层自检脚本（LLM / RAG / SSE / Agent / 并发） |

## 测试与自检

```bash
pytest -q                      # 单元测试（解析器 / 配置 / 单例 / 混合检索 / Prompt 回归）
python scripts/check_rag.py        # RAG 链路：入库 -> 检索
python scripts/check_stream.py     # SSE 真流式 + 引用来源（走 HTTP）
python scripts/check_agent.py      # Agent 端到端：意图路由 + 检索注入 + 流式 + 来源
python scripts/check_concurrent.py --mix   # 并发安全：混合端点压测
python scripts/eval_retrieval.py   # 检索评测：纯向量 vs 混合，recall@k / MRR 对比
python scripts/eval_answer.py      # LLM-as-judge：忠实度 / 相关性 1-5 打分
```

**评测怎么读**：`eval_retrieval.py` 用 14 条真实查询（专有名词类 + 语义改写类 + 跨域混合类）
对比两种检索模式。当前小语料（25 块）下两路 recall@3/@5 均 100%，混合检索的 MRR 略低
——BM25 会把字面相似但语义偏离的片段顶前（评测抓到的真实案例：问"求职者会哪些编程语言"，
提示词文档同样含"编程语言"字样被顶到第 1）。结论如实写进 README：**混合检索的收益预期在
更大语料和更多专有名词场景，配置开关保留，评测脚本可持续回归**。
`eval_answer.py` 则用裁判模型给回答打"忠实度/相关性"分——首轮评测即抓到一处真实幻觉
（回答引入了检索依据中不存在的"平移不变性"），证明该机制能定位"RAG 没压住幻觉"的具体环节。

## 工程难点复盘

1. **"假流式"是怎么来的**：最初直接 `await llm.ainvoke()` 再一次性 yield 整个结果，前端看起来是"文本突然全部出现"。改成 LangGraph `get_stream_writer()` 自定义流（节点内 `writer({"text": delta})`），配合 `stream_mode=["custom","values"]` 才拿到 token 级增量。
2. **并发冷启动 500**：uvicorn 把同步端点丢进线程池，多请求真并行；`chromadb.PersistentClient(...)` 构造时会初始化 Rust bindings 并校验 tenant，首次构造发生在请求线程里就会偶发 `ValueError: Could not connect to tenant default_tenant`（单请求永远正常）。解法：向量库/Embedding/Retriever 全部进程级单例（双检锁）+ 启动预热。同类问题还有 openai SDK 子模块惰性导入引发的 `_ModuleLock` 死锁。
3. **增量入库与"修改"共用一套幂等逻辑**：`doc_id = sha1(source + text)`，重复入库天然跳过；改块的文本会得到新 id，所以"修改 = 删旧 + 按新内容入库"，同一套去重规则复用，不会产生重复块。
4. **Windows 上传踩坑**：`shutil.move` 移动仍被打开的文件会报 `WinError 32`，改成直接 `write_bytes` 落盘 + 文件名取 `Path(filename).name` 防路径穿越。

## License

MIT
