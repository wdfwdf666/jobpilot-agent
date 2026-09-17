"""并发请求自检：验证后端在"混合端点并发"下不会因惰性 import 竞争而死锁。

背景（真踩过的坑）：
- uvicorn 把同步端点丢进线程池执行，多个请求会真正并行。
- 新版 openai SDK 对 resources 子模块做**惰性导入**：`from openai import OpenAI`
  只拿到类，`client.embeddings` / `client.chat` 这些属性首次访问时才 import
  `openai.resources.embeddings` / `openai.resources.chat`。
- 而客户端是每请求新建的，于是"首次 import"发生在线程里。两个线程同时触发
  且 import 顺序不同（embedding 链路 vs chat 链路）就会形成模块锁环，
  CPython 3.13+ 的 import 死锁检测直接抛：
  `deadlock detected by _ModuleLock('openai.resources.embeddings')`
- 表现极具迷惑性：单请求永远正常，一并发就偶发 500/卡住。

用法：
python scripts/check_concurrent.py                  # 5 并发，全部走 chat
python scripts/check_concurrent.py -n 6 --mix        # 混合 chat + kb/search（更易触发）
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

CHAT_URL = "http://127.0.0.1:8000/chat/stream"
SEARCH_URL = "http://127.0.0.1:8000/kb/search"


def hit_chat(i: int, url: str) -> tuple[int, str]:
    body = {"session_id": f"conc-{i}-{int(time.time())}", "message": "只回答两个字：收到"}
    try:
        with httpx.stream("POST", url, json=body, timeout=180) as resp:
            status = resp.status_code
            note = ""
            for line in resp.iter_lines():
                if line.startswith("data:") and '"message"' in line:
                    note = line[5:].strip()[:140]
            return status, note
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"[:140]


def hit_search(i: int, url: str) -> tuple[int, str]:
    body = {"query": f"GIL 并发 线程 {i}", "top_k": 2}
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, json=body)
        return resp.status_code, "" if resp.status_code == 200 else resp.text[:140]
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"[:140]


def main() -> None:
    parser = argparse.ArgumentParser(description="并发请求自检")
    parser.add_argument("-n", type=int, default=5, help="并发数")
    parser.add_argument("--url", default=CHAT_URL, help="chat/stream 地址")
    parser.add_argument("--search-url", default=SEARCH_URL, help="kb/search 地址")
    parser.add_argument("--mix", action="store_true", help="混合 chat 与 kb/search（更易触发竞态）")
    args = parser.parse_args()

    def task(i: int) -> tuple[int, str, str]:
        if args.mix and i % 2 == 1:
            status, note = hit_search(i, args.search_url)
            return status, note, "search"
        status, note = hit_chat(i, args.url)
        return status, note, "chat"

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.n) as pool:
        results = list(pool.map(task, range(args.n)))
    cost = time.time() - t0

    ok = sum(1 for s, _, _ in results if s == 200)
    print(f"并发数 {args.n}{'（混合 chat/search）' if args.mix else ''}，耗时 {cost:.1f}s")
    print(f"成功   {ok}/{args.n}")
    for i, (status, note, kind) in enumerate(results):
        flag = "OK  " if status == 200 else "FAIL"
        print(f"  [{flag}] #{i} {kind:<6} HTTP {status} {note}")
    print("结论:", "并发安全 ✓" if ok == args.n else "存在并发失败 ✗")


if __name__ == "__main__":
    main()
