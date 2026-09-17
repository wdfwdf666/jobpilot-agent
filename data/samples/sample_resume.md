# 张明 · AI Agent 应用开发工程师

基本信息
- 电话：13800000000 ｜ 邮箱：zhangming@example.com
- 求职意向：AI Agent 应用开发 / 大模型应用工程师 ｜ 期望城市：杭州
- GitHub：github.com/example-agent-dev

教育经历
- 2020.09 - 2023.06　某某大学　计算机技术　硕士
- 2016.09 - 2020.06　某某大学　软件工程　本科
- 主修课程：机器学习、分布式系统、数据库系统、软件工程

专业技能
- 编程语言：Python（主力）、TypeScript、Go（了解）
- 大模型应用：LangChain / LangGraph 多 Agent 编排、Function Calling、
  Prompt 工程、结构化输出约束、LLM-as-judge 评测
- RAG：文档解析（pdf/docx）、语义分块、向量检索（ChromaDB / pgvector）、
  BM25 混合检索与 RRF 融合、rerank 重排
- 工程：FastAPI、PostgreSQL、Redis、Docker、Git、Linux
- 模型 API：通义千问（百炼）、DeepSeek、OpenAI 兼容接口

工作经历
- 2023.07 - 至今　某某科技有限公司　AI 应用开发工程师
  - 负责企业知识库问答系统，从 0 到 1 搭建 RAG 链路，日均调用 3000+ 次
  - 主导检索优化：引入混合检索与重排后，答案命中率从 68% 提升到 89%
  - 沉淀 Prompt 模板与评测集，把上线前的回归测试从手工变成脚本自动化

项目经历
- JobPilot 智能求职助手 Agent（个人项目，2025.08 - 至今）
  - 技术栈：LangGraph + FastAPI + Qwen + ChromaDB + Vue 3
  - 设计 Supervisor 多 Agent 编排：Planner 意图路由到 JD 分析 / 简历顾问 /
    模拟面试三个子 Agent，共享状态并支持真流式输出（SSE 逐 token）
  - 实现简历板块级解析与增量入库，检索结果强制挂原文引用以抑制幻觉
  - 自建测试集 + LLM-as-judge 评测，量化匹配准确率
- 分布式任务调度平台（2022.03 - 2022.12）
  - 基于 Redis 实现分布式锁与任务幂等，支撑 10 万级日任务量
  - 设计失败重试与死信队列，任务失败率从 1.2% 降到 0.3%

荣誉奖项
- 2022 年全国大学生计算机设计大赛 省级二等奖
- 校级一等奖学金（2021、2022）

自我评价
- 对 LLM 应用工程有持续投入，习惯把踩过的坑沉淀成文档与脚本；
  工程上偏好可观测、可回滚的方案，不做黑盒调参。
