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
# 1. 创建虚拟环境并安装
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 2. 配置密钥
copy .env.example .env   # 填入 LLM_API_KEY / EMBED_API_KEY

# 3. 导入知识（支持 md/txt/pdf/docx）
python scripts/ingest.py path/to/notes.md --category 八股文

# 4. 启动后端（端口 8000）
uvicorn app.main:app --reload

# 5. 启动前端（新终端）
streamlit run frontend/app.py
```

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
