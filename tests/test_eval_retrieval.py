"""评测脚本的判定逻辑回归测试。

背景（真实事故）：第一版命中判定用子串匹配，关键词 "rag" 命中了
"Average Pooling"（ave-rag-e），加上"来源匹配即算命中"的宽松条件，
两种检索模式的 recall/MRR 全部虚高到 100%——指标失去分辨力。
这里把边界规则和 strict/loose 差异钉住。
"""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_retrieval.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_retrieval", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_retrieval"] = module
    spec.loader.exec_module(module)
    return module


def test_latin_keyword_uses_word_boundary():
    """拉丁关键词必须按词边界匹配，否则 'rag' 会命中 'Average'。"""
    mod = _load()
    assert mod.kw_in_text("我们使用 RAG 做检索增强", "RAG") is True
    assert mod.kw_in_text("平均池化（Average Pooling）", "RAG") is False   # 子串陷阱
    assert mod.kw_in_text("Max/AVG pooling", "AVG") is True
    assert mod.kw_in_text("langchain 工作流", "Chain") is False           # 长词内部不算
    assert mod.kw_in_text("LangChain1.3+LangGraph", "LangChain") is True  # 右边界允许数字


def test_chinese_keyword_substring():
    """中文没有词边界概念，直接子串匹配。"""
    mod = _load()
    assert mod.kw_in_text("池化层的主要功能是下采样", "池化") is True
    assert mod.kw_in_text("池化层的主要功能是下采样", "卷积") is False


def test_strict_requires_source_and_content():
    """strict：来源对得上还要内容真的对；loose：来源对就算命中（旧口径）。"""
    mod = _load()
    case = {"source": "王东风", "keywords": ["FastAPI"]}
    same_doc_but_wrong_chunk = {
        "text": "【项目经历】在校期间担任班长", "metadata": {"source": "王东风.pdf"},
    }
    assert mod.is_hit(same_doc_but_wrong_chunk, case) is False           # strict 正确拒绝
    assert mod.is_hit(same_doc_but_wrong_chunk, case, loose=True) is True  # loose 误判


def test_strict_accepts_right_chunk():
    mod = _load()
    case = {"source": "王东风", "keywords": ["FastAPI"]}
    hit = {"text": "熟悉 FastAPI、Vue、Linux", "metadata": {"source": "王东风.pdf"}}
    assert mod.is_hit(hit, case) is True


def test_category_scoped_case_matches_by_category():
    """简历类用例按 category 定位，不硬编码文件名（仓库公开时不带个人信息）。"""
    mod = _load()
    case = {"category": "简历素材", "keywords": ["LangGraph"]}
    hit = {"text": "熟悉 LangGraph 工作流编排", "metadata": {"category": "简历素材",
                                                          "source": "任意简历.md"}}
    assert mod.is_hit(hit, case) is True
    other = {"text": "熟悉 LangGraph", "metadata": {"category": "八股文", "source": "x.md"}}
    assert mod.is_hit(other, case) is False


def test_per_chunk_tags_length_mismatch_raises():
    """块级标签长度必须和块数一致，否则宁可直接报错（静默错配会污染元数据）。"""
    import pytest

    from app.rag.vectorstore import VectorStore

    with pytest.raises(ValueError, match="per_chunk_tags"):
        VectorStore.add_chunks(
            object.__new__(VectorStore), ["a", "b"], [[0.1], [0.2]],
            per_chunk_tags=[["x"]],  # 少一个
        )
