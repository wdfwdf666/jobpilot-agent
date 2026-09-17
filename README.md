# JobPilot · 智能求职助手 Agent

基于 **LangGraph 多 Agent 编排 + RAG + Function Calling** 的求职助手：
JD 深度分析 → 简历匹配与优化建议（带原文引用）→ 基于个人知识库的模拟面试。

> 详细设计见仓库外的《JobPilot_项目设计.md》。当前进度：**D1-2 已完成**
> （项目骨架 ✅ / 简历解析入库 ✅ / 检索与 LLM 打通 ✅：回答带原文引用与可追溯来源）。

## 架构

```
Streamlit 前端 ──> FastAPI (SSE 流式)
                      │
              LangGraph Supervisor
        ┌────────────┼────────────┐
     JD 分析     简历顾问      面试官
        └────────────┼────────────┘
        RAG 知识库（bge-m3 + ChromaDB）  工具集（搜索/解析/生成）
```

## 快速开始

```bash
# 1. 创建虚拟环境并安装（国内建议加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple）
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 2. 配置密钥（阿里云百炼，一份 Key 同时用于 LLM 和 Embedding）
copy .env.example .env   # 填入百炼 API Key：https://bailian.console.aliyun.com/
# 校验连通性（会顺带验证结构化输出与分批 embedding）
python scripts/check_llm.py
# 校验 RAG 链路（入库 -> 检索），可带自己的文件
python scripts/check_rag.py

# 3. 导入知识（支持 md/txt/pdf/docx/html）
python scripts/ingest.py path/to/notes.md --category 八股文

# 3b. 导入简历（自动按板块解析：教育/技能/工作/项目…，检索更精准）
python scripts/ingest_resume.py "path/to/简历.pdf"
# 简历改版后重传：清掉同源旧块，避免旧版本残留干扰检索
python scripts/ingest_resume.py "path/to/简历.pdf" --replace
# 想立刻验证"检索 + LLM 带原文引用回答"这条链路：
python scripts/ingest_resume.py "path/to/简历.docx" --query "简历里的项目经历有什么可优化的"

# 4. 启动后端（端口 8000）。必须先起后端再起前端
uvicorn app.main:app --reload
# 启动日志里会打印 warmup 报告：{"vector_store":"ok (29 chunks)", ...}
# 这一步把 chroma/openai 的重初始化提前到单线程阶段，避免并发冷启动竞态

# 5. 启动前端（新终端，端口 5173，/api 代理到 8000）
cd frontend
npm install
npm run dev
```

## 技术选型

| 能力 | 选型 | 说明 |
|------|------|------|
| LLM | `qwen-plus`（百炼） | 1M 上下文、¥0.8/百万输入 token，能力与成本均衡 |
| Embedding | `text-embedding-v4`（Qwen3-Embedding 系列） | 1024 维可调、¥0.07/百万 token、单批上限 10 条（代码已自动分批） |
| 向量库 | ChromaDB（cosine） | 轻量零运维，后续可换 Milvus/pgvector |
| 编排 | LangGraph | 显式状态图，比隐式 Agent 链可控可回溯 |

> 换模型只需改 `.env`，代码零改动 —— 这也是面试可讲的抽象设计点。

## 模块说明

| 路径 | 职责 | 状态 |
|------|------|------|
| `app/config.py` | 全局配置（pydantic-settings，读 .env） | ✅ |
| `app/rag/` | 文档解析（pdf/docx/html）→ 简历板块解析 → 分块 → 向量化 → 检索 | ✅ |
| `app/agents/` | Planner + 三个子 Agent + LangGraph 编排（真流式，检索按类别注入上下文） | ✅ |
| `app/api/` | REST + SSE 流式接口、知识库管理接口（category=简历素材 自动走简历解析） | ✅ |
| `app/eval/` | LLM-as-judge 评测（第 2 周） | 占位 |
| `frontend/` | Vue 3 + TS 对话界面（markdown 渲染 + 流式 + 引用来源可追溯 + 知识库管理） | ✅ |
| `scripts/ingest.py` | 知识入库 CLI | ✅ |
| `scripts/ingest_resume.py` | 简历解析入库 CLI（板块预览、--replace、--query 验证） | ✅ |
| `scripts/check_llm.py` | LLM/Embedding 连通性自检 | ✅ |
| `scripts/check_rag.py` | RAG 链路自检（入库→检索） | ✅ |
| `scripts/check_stream.py` | SSE 真流式 + 引用来源自检（走 HTTP） | ✅ |
| `scripts/check_agent.py` | Agent 端到端自检（意图路由 + 检索注入 + 流式 + 来源） | ✅ |
| `scripts/check_concurrent.py` | 并发安全自检（`--mix` 混合端点压测，验重客户端单例） | ✅ |
| `app/warmup.py` | 启动预热：消除冷启动并发竞态（惰性 import + 重客户端初始化） | ✅ |
