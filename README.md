# JobPilot · 智能求职助手 Agent

基于 **LangGraph 多 Agent 编排 + RAG + Function Calling** 的求职助手：
JD 深度分析 → 简历匹配与优化建议（带原文引用）→ 基于个人知识库的模拟面试。

> 详细设计见仓库外的《JobPilot_项目设计.md》。当前进度：**骨架阶段**。

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

# 3. 导入知识（支持 md/txt/pdf/docx）
python scripts/ingest.py path/to/notes.md --category 八股文

# 4. 启动后端（端口 8000）
uvicorn app.main:app --reload

# 5. 启动前端（新终端）
streamlit run frontend/app.py
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
| `app/rag/` | 文档解析 → 分块 → 向量化 → 检索（V1 向量 / V2 混合） | 骨架 |
| `app/agents/` | Planner + 三个子 Agent + LangGraph 编排 | 骨架 |
| `app/api/` | REST + SSE 流式接口、知识库管理接口 | 骨架 |
| `app/eval/` | LLM-as-judge 评测（第 2 周） | 占位 |
| `frontend/` | Streamlit 聊天界面 | 骨架 |
| `scripts/ingest.py` | 知识入库 CLI | ✅ |
| `scripts/check_llm.py` | LLM/Embedding 连通性自检 | ✅ |
| `scripts/check_rag.py` | RAG 链路端到端自检（入库→检索） | ✅ |
