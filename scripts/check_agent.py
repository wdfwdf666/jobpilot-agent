"""Agent 端到端自检（不经过 HTTP，直接驱动 LangGraph）：

验证四件事：
1. Planner 意图路由是否正确；
2. 节点内检索是否真的按类别召回（简历素材 / 八股文）、并注入 LLM 上下文；
3. custom stream 增量是否陆续到达（真流式）；
4. 引用来源是否随 artifacts 返回（可追溯）。

用法：
python scripts/check_agent.py "帮我看看简历里的项目经历有什么可以优化的"
python scripts/check_agent.py "我们开始一场 Python 后端面试" --show-sources
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.graph import app_graph  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="JobPilot Agent 端到端自检")
    parser.add_argument("message", help="发给 Agent 的消息")
    parser.add_argument("--show-sources", action="store_true", help="打印检索命中的原文片段")
    args = parser.parse_args()

    t0 = time.time()
    delta_times: list[float] = []
    deltas: list[str] = []
    statuses: list[str] = []
    final_state: dict = {}

    stream = app_graph.stream(
        {
            "messages": [],
            "user_input": args.message,
            "intent": "",
            "reply": "",
            "artifacts": {},
        },
        stream_mode=["custom", "values"],
    )
    for mode, chunk in stream:
        if mode == "custom":
            if "delta" in chunk:
                deltas.append(chunk["delta"])
                delta_times.append(time.time() - t0)
            elif "status" in chunk:
                statuses.append(chunk["status"])
        else:
            final_state = chunk

    text = "".join(deltas) or final_state.get("reply", "")
    artifacts = final_state.get("artifacts", {}) or {}
    sources = artifacts.get("sources", [])

    print(f"意图路由        : {final_state.get('intent')}")
    print(f"阶段状态事件    : {statuses or '（无，直接生成）'}")
    print(f"增量数 / 总字符 : {len(deltas)} / {len(text)}")
    if len(delta_times) >= 2:
        span = delta_times[-1] - delta_times[0]
        print(f"流式跨度        : {delta_times[0]:.2f}s -> {delta_times[-1]:.2f}s（{span:.2f}s）"
              f" => {'真流式 OK' if span > 0.8 else '疑似未流式'}")
    print(f"引用来源        : {len(sources)} 段")
    if args.show_sources and sources:
        for s in sources:
            print(f"  - [{s['category']}] {s['source']} (距离 {s['distance']}): {s['text'][:70]}…")
    print(f"结构化产物      : {[k for k in artifacts] or '（无）'}")
    print("\n--- 回复（前 400 字）---")
    print(text[:400])


if __name__ == "__main__":
    main()
