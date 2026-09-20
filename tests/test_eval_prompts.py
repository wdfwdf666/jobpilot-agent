"""评测脚本的 Prompt 回归测试。

背景（真实事故，同型 bug 第二次）：eval_answer.JUDGE_PROMPT 的 JSON 示例
没转义花括号，str.format() 把 {"faithfulness":...} 当占位符 -> KeyError。
test_prompts.py 已覆盖 app 内的三个模板，这里覆盖 scripts/ 下的评测模板。
"""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_answer.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("eval_answer", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_answer"] = module
    spec.loader.exec_module(module)
    return module


def test_judge_prompt_format_ok():
    """JSON 示例的花括号必须转义：format 后原样保留给模型看。"""
    mod = _load_script_module()
    out = mod.JUDGE_PROMPT.format(
        question="GIL 是什么", context="- 全局解释器锁...", answer="GIL 是..."
    )
    assert '"faithfulness"' in out
    assert "GIL 是什么" in out
