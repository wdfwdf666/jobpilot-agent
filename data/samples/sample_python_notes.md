# Python 基础八股文（示例知识）

## GIL 是什么，为什么会有它
GIL（全局解释器锁）是 CPython 解释器的一把互斥锁，保证同一时刻只有一个线程执行 Python 字节码。
它的存在源于 CPython 的内存管理（引用计数）不是线程安全的，加锁实现最简单。
影响：CPU 密集型任务无法靠多线程获得多核加速，应改用多进程（multiprocessing）或 C 扩展释放 GIL；
IO 密集型任务因线程在等待 IO 时会释放 GIL，多线程仍然有效。

## asyncio 与多线程的区别
asyncio 是单线程事件循环 + 协程协作式调度，切换由 await 显式让出，没有线程切换开销，
适合高并发 IO；多线程是抢占式调度，适合阻塞式库的并行 IO。
注意：asyncio 中一旦出现同步阻塞调用（如 time.sleep、requests.get），整个事件循环会被卡住，
必须用 asyncio.to_thread 或换成异步库（aiohttp/httpx）。

## 装饰器的本质
装饰器是一个接收函数并返回函数的高阶函数，@decorator 等价于 func = decorator(func)。
带参数的装饰器需要三层嵌套。保留原函数元信息要用 functools.wraps。
典型用途：日志、鉴权、缓存（lru_cache）、重试。

## 浅拷贝与深拷贝
copy.copy 只复制最外层对象，内部嵌套对象仍共享引用；copy.deepcopy 递归复制所有层级。
不可变对象（int/str/tuple）的"拷贝"通常直接返回自身引用。
