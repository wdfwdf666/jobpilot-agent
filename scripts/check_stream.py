"""真流式验证：打印每个 SSE 事件到达的时间差。真流式应看到增量在数秒内陆续到达，
而不是除第一个外全部同时到达（伪流式特征）。"""
import time

import httpx

URL = "http://127.0.0.1:8000/chat/stream"
body = {"session_id": f"stream-test-{int(time.time())}", "message": "用 150 字左右介绍 Python GIL"}

t0 = time.time()
delta_times: list[float] = []
with httpx.stream("POST", URL, json=body, timeout=120) as resp:
    print(f"HTTP {resp.status_code}")
    for line in resp.iter_lines():
        if not line.startswith("data:") or '"text"' not in line:
            continue
        delta_times.append(time.time() - t0)

print(f"delta 事件数: {len(delta_times)}")
if len(delta_times) >= 2:
    span = delta_times[-1] - delta_times[0]
    print(f"首增量: {delta_times[0]:.2f}s，末增量: {delta_times[-1]:.2f}s，跨度: {span:.2f}s")
    print("VERDICT:", "真流式 OK" if span > 1.0 else "仍是伪流式 FAIL")
