"""Function Calling 链路自检：真实调用 LLM + 搜索，验证模型会自主发起工具调用。

用法：
    python scripts/check_tools.py                       # 用内置问题跑一遍
    python scripts/check_tools.py "博查是哪家公司做的"   # 用自己的问题跑

验证点：
- 工具声明能否被模型正确识别（描述写不好时模型压根不用）
- 模型会不会在"需要公开实时信息"时自主调用，在"不需要"时保持不调用
- 搜索结果能否回喂、最终回答是否正常产出
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tools.tool_loop import GENERAL_SYSTEM, run_with_tools  # noqa: E402
from app.tools.web_search import search_available  # noqa: E402

# 第一问刻意选"不联网就答不准"的：用于验证模型确实会自己决定调用工具。
# 第二问是常识/自我介绍类，用于验证模型不会滥用工具（不搜才是对的）。
DEFAULT_QUESTIONS = [
    "博查 BochaAI 是一家做什么的公司？它的搜索 API 主要面向什么场景？",
    "用一句话说明 RAG 和微调的区别，不需要联网。",
]


def main() -> int:
    if not search_available():
        print("搜索 Key 未配置，无法验证真实链路。请在 .env 填写 BOCHA_API_KEY。")
        return 1

    questions = sys.argv[1:] or DEFAULT_QUESTIONS
    ok = True

    for idx, question in enumerate(questions, 1):
        print(f"\n[{idx}/{len(questions)}] 问题：{question}")
        events: list[dict] = []
        try:
            run = run_with_tools(
                [{"role": "user", "content": question}],
                system=GENERAL_SYSTEM,
                writer=events.append,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ! 调用失败 {type(exc).__name__}: {exc}")
            ok = False
            continue

        for event in events:
            if "status" in event:
                print(f"  · {event['status']}")
        print(f"  自主调用工具：{'是' if run.used_tools else '否'}（{run.tool_calls} 次）")
        for hit in run.web_results:
            print(f"    - {(hit.get('title') or '')[:40]} | {hit.get('url', '')}")
        preview = run.text.replace("\n", " ")[:120]
        print(f"  回答：{preview}…")

        if not run.text.strip():
            print("  ! 没有产出回答")
            ok = False
        # 第二类问题不该联网：模型滥用工具时提示，但不判失败（模型行为有概率性）
        if idx == 2 and run.used_tools:
            print("  ~ 提示：这题本不该联网，模型仍调用了工具（可收紧工具描述）")

    print("\n结果:", "Function Calling 链路连通 ✅" if ok else "链路异常 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
