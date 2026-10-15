"""模拟面试的报告接口：把逐题评分聚合成可下载的 Markdown，并给出知识库回链。

为什么单独一个 router：会话/流式走 /chat，面试的结果沉淀走 /interview，
职责边界清晰；将来加"面试题库管理""历史报告列表"也挂这里。
"""
from fastapi import APIRouter

from app.agents.graph import get_interviewer

router = APIRouter(prefix="/interview", tags=["interview"])


@router.get("/report/{session_id}")
def get_report(session_id: str) -> dict:
    """导出本场面试的评分报告。

    返回 markdown（前端直接下载）+ knowledge_gaps（前端渲染成"去补这块"的跳转）。
    """
    return get_interviewer(session_id).build_report(session_id=session_id)


@router.get("/records/{session_id}")
def get_records(session_id: str) -> dict:
    """只取逐题评分记录（调试/二次加工用，不生成 markdown）。"""
    agent = get_interviewer(session_id)
    return {"records": agent.records, "pending_question": agent.pending_question}
