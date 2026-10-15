"""Agent 端到端自检（不经过 HTTP，直接驱动 LangGraph）：

验证五件事：
1. Planner 意图路由是否正确；
2. 节点内检索是否真的按类别召回（简历素材 / 八股文）、并注入 LLM 上下文；
3. custom stream 增量是否陆续到达（真流式）；
4. 引用来源是否随 artifacts 返回（可追溯）；
5. JD 分析的**人工审核中断**是否触发，以及 resume 后能否续跑（LangGraph 人机协同）。

用法：
python scripts/check_agent.py "帮我看看简历里的项目经历有什么可以优化的"
python scripts/check_agent.py "我们开始一场 Python 后端面试" --show-sources
python scripts/check_agent.py "分析这个 JD：..." --hold    # 停在人工审核处，不自动恢复
"""
import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command  # noqa: E402

from app.agents.graph import app_graph  # noqa: E402


def _drain(stream, t0: float, deltas: list[str], delta_times: list[float],
           statuses: list[str]) -> tuple[dict, object]:
    final_state: dict = {}
    interrupts = None
    for mode, chunk in stream:
        if mode == "custom":
            if "delta" in chunk:
                deltas.append(chunk["delta"])
                delta_times.append(time.time() - t0)
            elif "status" in chunk:
                statuses.append(chunk["status"])
        else:
            final_state = chunk
            if chunk.get("__interrupt__"):
                interrupts = chunk["__interrupt__"]
    return final_state, interrupts


def main() -> None:
    parser = argparse.ArgumentParser(description="JobPilot Agent 端到端自检")
    parser.add_argument("message", help="发给 Agent 的消息")
    parser.add_argument("--show-sources", action="store_true", help="打印检索命中的原文片段")
    parser.add_argument("--hold", action="store_true",
                        help="命中人工审核时不自动恢复，打印中断载荷后退出")
    args = parser.parse_args()

    # thread_id 必须显式给：图带 checkpointer 编译后，中断与状态都按线程归属
    # （不传时 LangGraph 无法保存断点，interrupt 会直接报错）
    thread_id = f"check-agent-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    t0 = time.time()
    deltas: list[str] = []
    delta_times: list[float] = []
    statuses: list[str] = []

    final_state, interrupts = _drain(
        app_graph.stream(
            {
                "messages": [{"role": "user", "content": args.message}],
                "user_input": args.message,
                "session_id": thread_id,
                "force_intent": "auto",
                "intent": "",
                "reply": "",
                "artifacts": {},
            },
            config=config,
            stream_mode=["custom", "values"],
        ),
        t0, deltas, delta_times, statuses,
    )

    if interrupts:
        payload = interrupts[0].value
        print(f"人工审核中断    : {payload.get('type')} —— 岗位【{payload.get('position')}】")
        print(f"抽取硬性要求    : {payload.get('hard_requirements')}")
        if args.hold:
            print(f"\n已停在中断处（thread_id={thread_id}）。"
                  f"去掉 --hold 可自动确认并继续；Web 端则是在审核卡片上点确认。")
            return
        print("自动确认并续跑  : resume({'action': 'approve'}) …\n")
        final_state, interrupts = _drain(
            app_graph.stream(Command(resume={"action": "approve"}), config=config,
                             stream_mode=["custom", "values"]),
            t0, deltas, delta_times, statuses,
        )

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
    if artifacts.get("match_result"):
        print(f"匹配度          : {artifacts['match_result'].get('overall_score')}/100"
              f"（{len(artifacts['match_result'].get('items', []))} 条要求）")
    print(f"历史消息数      : {len(final_state.get('messages', []))}（检查点持久化）")
    print("\n--- 回复（前 400 字）---")
    print(text[:400])


if __name__ == "__main__":
    main()
