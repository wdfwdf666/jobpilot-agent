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
| `scripts/eval_retrieval.py` | 检索评测：recall@k / MRR + 逐条诊断 + 语料一致性自检 |
| `scripts/eval_answer.py` | LLM-as-judge：忠实度 / 相关性打分 + 基线对比 |
| `scripts/` | 入库 CLI（知识/简历）+ 各层自检脚本（LLM / RAG / SSE / Agent / 并发） |

## 测试与自检

```bash
pytest -q                      # 单元测试（解析器 / 配置 / 单例 / 混合检索 / Prompt 回归）
python scripts/check_rag.py        # RAG 链路：入库 -> 检索
python scripts/check_stream.py     # SSE 真流式 + 引用来源（走 HTTP）
python scripts/check_agent.py      # Agent 端到端：意图路由 + 检索注入 + 流式 + 来源
python scripts/check_concurrent.py --mix   # 并发安全：混合端点压测
python scripts/eval_retrieval.py --check-corpus   # 【改库后先跑】用例是否已过期
python scripts/eval_retrieval.py -v               # 检索评测 + 逐条诊断
python scripts/eval_answer.py -v                  # LLM-as-judge + 依据/回答明细
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

## License

MIT
