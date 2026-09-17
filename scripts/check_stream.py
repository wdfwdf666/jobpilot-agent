"""SSE 端到端验证：走真实 HTTP，检查增量到达节奏、意图、引用来源。

用法：
python scripts/check_stream.py
python scripts/check_stream.py --message "帮我看看简历里的项目经历有什么可以优化的"
"""
import argparse
import json
import time

import httpx

URL = "http://127.0.0.1:8000/chat/stream"


def main() -> None:
    parser = argparse.ArgumentParser(description="SSE 真流式 + 引用来源验证")
    parser.add_argument("--message", default="用 150 字左右介绍 Python GIL")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args()

    body = {"session_id": f"stream-test-{int(time.time())}", "message": args.message}
    t0 = time.time()
    delta_times: list[float] = []
    statuses: list[str] = []
    sources: list[dict] = []
    intent = ""
    text_len = 0

    try:
        with httpx.stream("POST", args.url, json=body, timeout=180) as resp:
            print(f"HTTP {resp.status_code}")
            event = ""
            for line in resp.iter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if event == "delta":
                    text_len += len(payload)
                    delta_times.append(time.time() - t0)
                elif event == "status":
                    statuses.append(json.loads(payload).get("text", ""))
                elif event == "artifacts":
                    sources = json.loads(payload).get("sources", []) or []
                elif event == "done":
                    intent = json.loads(payload).get("intent", "")
                elif event == "error":
                    print(f"后端报错: {payload}")
                    return
    except httpx.ConnectError:
        print("连接失败：后端未启动（先跑 uvicorn app.main:app --reload）")
        return

    print(f"意图路由     : {intent}")
    print(f"阶段状态     : {statuses or '（无）'}")
    print(f"增量数/总字符: {len(delta_times)} / ~{text_len}")
    if len(delta_times) >= 2:
        span = delta_times[-1] - delta_times[0]
        print(f"流式跨度     : {delta_times[0]:.2f}s -> {delta_times[-1]:.2f}s（{span:.2f}s）"
              f" => {'真流式 OK' if span > 0.8 else '疑似未流式'}")
    print(f"引用来源     : {len(sources)} 段" + (f"（首段: {sources[0]['text'][:50]}…）" if sources else ""))


if __name__ == "__main__":
    main()
