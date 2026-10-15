"""面试评分报告的聚合测试。

刻意绕开 InterviewerAgent.__init__（它会去初始化真实 Retriever / 向量库），
用 object.__new__ 直接构造并注入假检索器——单测只该验证聚合逻辑本身。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.interviewer import InterviewerAgent  # noqa: E402
from app.schemas import RetrievedChunk  # noqa: E402


class _FakeRetriever:
    def search(self, query, top_k=5, category=None):
        return [{
            "text": "RRF 用倒数排名融合多路召回，k 越小越奖励头部名次",
            "metadata": {"source": "八股文.md", "category": "八股文"},
            "distance": 0.3,
        }]


def _agent() -> InterviewerAgent:
    agent = object.__new__(InterviewerAgent)
    agent._retriever = _FakeRetriever()
    agent._history = []
    agent._pending_question = None
    agent._records = []
    agent._ended = False
    return agent


def _grade(score, missing, followup="再想想"):
    return {
        "score": score,
        "correct_points": ["答对了定义"],
        "missing_points": missing,
        "followup": followup,
    }


def test_empty_report_is_graceful():
    report = _agent().build_report(session_id="s1")
    assert report["turns"] == 0
    assert report["average_score"] == 0
    assert report["knowledge_gaps"] == []
    assert "还没有产生评分记录" in report["markdown"]


def test_report_aggregates_scores_and_gaps():
    agent = _agent()
    refs = [RetrievedChunk(text="RRF 融合要点", source="八股文.md", category="八股文")]
    agent._record("什么是 RRF 融合？", "用倒数排名", _grade(60, ["RRF 的 k 参数作用"]), refs)
    agent._record("解释一下 GIL", "全局解释器锁", _grade(90, []), refs)

    report = agent.build_report(session_id="s1")

    assert report["turns"] == 2
    assert report["average_score"] == 75
    md = report["markdown"]
    assert "# 模拟面试评分报告" in md
    assert "| 1 | 什么是 RRF 融合？ | 60 |" in md
    assert "平均得分：**75/100**" in md
    # 只有缺失要点的题才进"待补强"，答满分的题不该出现
    assert len(report["knowledge_gaps"]) == 1
    gap = report["knowledge_gaps"][0]
    assert gap["missing_points"] == ["RRF 的 k 参数作用"]
    assert gap["category"] == "八股文"
    assert "RRF 的 k 参数作用" in gap["query"]
    # 回链落到具体文档，而不是一句"多看看"
    assert gap["hits"][0]["source"] == "八股文.md"
    assert "八股文.md" in md


def test_score_is_clamped_and_history_survives_end():
    agent = _agent()
    refs: list[RetrievedChunk] = []
    agent._record("题", "答", _grade(140, ["a"]), refs)
    agent._record("题2", "答", _grade(-5, []), refs)
    assert [r["score"] for r in agent._records] == [100, 0]

    # 结束面试后记录必须保留——报告是"结束之后"才导出的
    agent._ended = True
    assert len(agent.records) == 2
    assert agent.build_report()["turns"] == 2


def test_gap_query_falls_back_to_question_when_points_blank():
    agent = _agent()
    agent._record("谈谈你对 RAG 的理解", "嗯", _grade(50, []), [])
    # missing_points 为空 -> 不产生回链项，但明细里题目仍在
    assert agent.build_report()["knowledge_gaps"] == []
    assert "谈谈你对 RAG 的理解" in agent.build_report()["markdown"]
