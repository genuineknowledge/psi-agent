# asyncio vs uvloop 实现原理与性能差异深度对比

## 摘要

asyncio 是建立在「selector 就绪通知 + Python 回调队列 + heapq 定时器堆」之上的纯 Python/部分 C 加速单线程调度器；uvloop 用 Cython 在 libuv 之上重写了等价循环，把热路径整体下沉到 C。二者底层都用 epoll/kqueue，性能差距源于 Python↔C 边界跨越次数，而非多路复用本身。网络密集场景 uvloop 通常快 2–4x。

## 对比总表

| 维度 | asyncio | uvloop | 胜出 |
|------|---------|--------|------|
| 循环驱动模型 | Python `while` 每圈调 `_run_once()`（`base_events.py`） | `uv_run(UV_RUN_DEFAULT)` 长驻 C，idle 句柄驱动就绪队列（`loop.pyx`） | uvloop ✅ |
| 与 asyncio 基类关系 | 自身即 `BaseEventLoop` 实现 | 不继承 `BaseEventLoop`，仅混入 `AbstractEventLoop` 接口 | 平手（设计取舍） |
| I/O 多路复用 | `selectors.py`（epoll/kqueue/poll/select） | libuv `uv__io_poll()`（同样 epoll/kqueue） | 平手（底层相同）✅ |
| 就绪队列处理 | Python `_run_once` 阶段 3 | Cython `_on_idle()`（编译后即 C） | uvloop ✅ |
| Transport 读路径 | Python `sock.recv()` 每次进解释器 | `uv_read_start` C 回调，仅 `data_received` 跨入 Python | uvloop ✅ |
| 写路径快路径 | 直接 `sock.send()`，剩余入 `_buffer` | `uv_try_write()` 零分配快写，失败才 `uv_write()` | uvloop ✅ |
| 定时器 | Python `heapq` 维护 `_scheduled` | libuv `uv_timer_t` 定时堆（C） | uvloop ✅ |
| 跨线程唤醒 | self-pipe + `_write_to_self()`（write syscall） | `uv_async_send()`（`async_.pyx`） | uvloop ✅ |
| 协程切换热路径 | `_asyncio` C 扩展 `task_step_impl`（共用） | 沿用同一 CPython 协程机制 | 平手 ✅ |
| 信号处理 | self-pipe + `set_wakeup_fd` | 同样 self-pipe + `set_wakeup_fd`（非 `uv_signal_t`） | 平手 |
| 子进程回收 | child watcher + `SIGCHLD` + `waitpid` | libuv 统一处理 `SIGCHLD`（`uv_spawn`） | uvloop ✅ |
| DNS 解析 | 默认 executor 线程池跑 `getaddrinfo` | libuv 线程池 + 数值地址快路径（`dns.pyx`） | uvloop ✅ |
| Windows 支持 | `ProactorEventLoop`（IOCP）原生支持 | 完全不支持 | asyncio ✅ |
| 可调试性/兼容性 | 栈贴近文档，selector 内部可访问 | 栈为 Cython，私有内部访问有风险 | asyncio ✅ |
| CPU 密集负载 | 无差异 | 无差异（收益甚微） | 平手 |
| 网络吞吐（1KB echo） | 约 0.5 Gbps | 约 2+ Gbps | uvloop ✅ |

## 1. 架构差异

### 1.1 事件循环模型：Python while 驱动 vs C 长驻

asyncio 的运行核心是 `BaseEventLoop.run_forever()`（`cpython/Lib/asyncio/base_events.py`），它在 **Python 层**跑一个 `while` 循环，每圈调用一次 `_run_once()`：

```python
def run_forever(self):
    while True:
        self._run_once()      # 每转一圈回到 Python
        if self._stopping:
            break
```

`_run_once()` 单次迭代严格三阶段：算超时 → `self._selector.select(timeout)` → `_process_events` 把就绪 fd 回调塞入 `_ready` → 出堆到期 `TimerHandle` → 用 `ntodo = len(self._ready)` 做长度快照后串行执行回调。`ntodo` 快照是关键设计：本轮新 `call_soon` 进来的回调留到下一轮，保证公平性与可终止性。

uvloop 把控制权**整体交给 libuv**，Python 层没有 while 循环（`uvloop/loop.pyx`）：

```python
cdef _run(self, uv.uv_run_mode mode):
    self._running = 1
    with nogil:                        # 释放 GIL,完全进入 C
        err = uv.uv_run(self.uvloop, mode)   # mode = UV_RUN_DEFAULT
```

进入 `uv_run(UV_RUN_DEFAULT)`（`src/unix/core.c`）后，循环长期停留在 C 里，连 epoll_wait 阻塞都在此处，只在触发回调时才回到 Cython/Python。libuv 每轮固定阶段顺序为：更新时间 → 跑到期定时器 → pending 回调 → idle handles → prepare handles → `uv__io_poll` 阻塞 → check handles → close 回调。

那 asyncio 的 `call_soon` 就绪队列在 uvloop 里怎么跑？答案是一个 **idle 句柄**桥接（`uvloop/handles/idle.pyx`）。libuv 规定：只要有活动 idle 句柄，`uv_backend_timeout` 返回 0（epoll 不阻塞），且每轮都会调 idle 回调，排干 `_ready`：

```python
cdef _on_idle(self):
    cdef int i, ntodo = len(self._ready)
    for i in range(ntodo):
        handler = <Handle> self._ready.popleft()
        if not handler._cancelled:
            handler._run()
    if len(self._ready) == 0:
        self.handler_idle.stop()   # 队列空了,允许 epoll 重新阻塞
    if self._stopping:
        uv.uv_stop(self.uvloop)
```

注意一个关键点：uvloop **并不**在 Python 里反复调 `UV_RUN_ONCE`（那才是与 `_run_once` 直接对应的朴素写法），而是用 `UV_RUN_DEFAULT` 长驻 + idle 句柄内部驱动 + `uv_stop` 收尾，把每轮迭代的 Python↔C 边界跨越降到接近零。

还有一个常被误解的事实需要纠正：**uvloop 的 `Loop` 不继承 `asyncio.BaseEventLoop`**，而是用 Cython 的 `cdef class Loop`（`uvloop/loop.pyx`，声明在 `loop.pxd`）从零实现，独立持有 `uv_loop_t* uvloop`。它只混入 `asyncio.AbstractEventLoop`（纯抽象接口）：

```python
from .loop import Loop as __BaseLoop
class Loop(__BaseLoop, asyncio.AbstractEventLoop):
    pass
```

混入抽象接口只为让 `isinstance(loop, asyncio.AbstractEventLoop)` 成立、满足鸭子类型期望。因为不复用 `BaseEventLoop`，asyncio 那套基于 `selectors` 的 `select` + Python 层 `sock.recv/send` 实现被整体绕开——这正是性能差异的结构性来源。

### 1.2 I/O 多路复用：同底层，不同封装

两者底层多路复用机制其实**一样**，差别在封装位置。

asyncio 走 `Lib/selectors.py`，按平台能力降级：`EpollSelector`（Linux）→ `KqueueSelector`（BSD/mac）→ `DevpollSelector`（Solaris）→ `PollSelector` → `SelectSelector`（兜底）。`BaseSelectorEventLoop._process_events()`（`selector_events.py`）遍历 `event_list`，从 `SelectorKey.data` 取出 `(reader, writer)` 两个 `Handle`，按 `EVENT_READ`/`EVENT_WRITE` 分发。值得强调：IOCP **不在** `selectors` 体系内，Windows 的 `ProactorEventLoop` 用 `_overlapped` C 扩展（`Modules/overlapped.c`）调 `GetQueuedCompletionStatus`——这是 selector（就绪通知/readiness）与 proactor（完成通知/completion）两种模型的本质区别。

libuv 把多路复用收敛到内部 watcher 抽象 `uv__io_t` 和统一入口 `uv__io_poll()`：Linux epoll 在 `src/unix/linux-core.c`，macOS/BSD kqueue 在 `src/unix/kqueue.c`，SunOS event ports 在 `src/unix/sunos.c`，Windows IOCP 在 `src/win/core.c`（proactor 语义）。`uv_backend_timeout(loop)` 直接基于最近定时器与 idle 状态算出本轮阻塞超时，省去 asyncio 在 Python 层算超时的开销。

结论：**「libuv 比 epoll 快」是错误认知**——底层 syscall 完全相同。差距来自调用这些 syscall 的外壳是 Python 解释器还是编译后的 C。

### 1.3 协程切换路径：共享同一套 CPython 机制

这里两者**没有本质差异**，因为协程驱动是 CPython 解释器的事，不归事件循环管。

`async def` 在编译期（`Python/compile.c`）给 code object 打 `CO_COROUTINE` 标志，构造 `PyCoro_Type`（`Objects/genobject.c`，与生成器共享底层帧机）。`await expr` 先 `GET_AWAITABLE`（`Python/ceval.c`），3.10 及以前进入 `YIELD_FROM` 委托，3.11+ 改用 `SEND` + `YIELD_VALUE` 配合零开销帧（PEP 659），委托效率显著提升。

asyncio 的 Task 有两份实现：纯 Python 版 `Lib/asyncio/tasks.py` 和 C 加速版 `TaskObj`（`Modules/_asynciomodule.c`，导入时若 `_asyncio` 可用则覆盖）。热路径 `task_step` → `task_step_impl` 全程在 C 层完成：用 `PyIter_Send()`/`_PyGen_Send()` 向协程发值，是 Future 则调 `future_add_done_callback` 注册 `task_wakeup`，是 `StopIteration` 则 `future_set_result`。整条「send → 分发 → 注册回调 → wakeup」避开了 Python 字节码解释和方法查找，是 3.6+ 协程吞吐提升的主因。

uvloop **沿用同一套 CPython 协程机制和 `_asyncio` 加速**，它优化的是循环侧（回调对象、就绪队列、传输读写），而非协程 send/throw 本身。uvloop 的 `Handle`/`TimerHandle` 等价实现在 `uvloop/cbhandles.pyx`（`cdef class`，比 asyncio 纯 Python 的 `events.Handle` 轻量），但协程切换路径与 asyncio 一致。这解释了为什么 uvloop 对纯计算型协程几乎无加速——它的优化全在 I/O 边界。

## 2. 关键路径性能对比

### 2.1 一次网络读的边界跨越对比

| 步骤 | asyncio | uvloop |
|------|---------|--------|
| 等待 I/O | Python 调 `selector.select(timeout)` | C 内 `uv__io_poll`（epoll_wait），`with nogil` 释放 GIL |
| 发现就绪 | Python 遍历就绪 `SelectorKey` | C 回调 `__uv_stream_on_read` 直接触发 |
| 读数据 | Python 调 `transport._read_ready` → `sock.recv()` | C 级 alloc + `uv_read_start` 回调，复用共享 `_recv_buffer` |
| 构造对象 | Python 构造 `bytes` | C 从共享缓冲构造 `bytes`（一次拷贝） |
| 进用户码 | Python 调 `protocol.data_received` | **仅此步**跨入 Python |
| 边界跨越次数 | 多次进出解释器 + 大量属性查找 + 临时对象 | 接近 1 次（仅 `data_received`） |

asyncio 一次读路径（`_SelectorSocketTransport._read_ready__data_received`，`selector_events.py`）：`sock.recv()` → `protocol.data_received(data)`，空字节即 EOF → `protocol.eof_received()`。缓冲协议（`BufferedProtocol`，3.7+）走 `_read_ready__get_buffer`：`get_buffer()` → `sock.recv_into()` 零拷贝填充 → `buffer_updated(nbytes)`。

uvloop 的读侧（`uvloop/handles/stream.pyx`）不注册到 selector，而是 `uv_read_start()` 一次性挂上 C 级 alloc + read 回调。`Loop` 持有固定大小共享接收缓冲 `_recv_buffer`（大小见 `includes/consts.pxi`，量级百 KB），alloc 回调把共享缓冲指针交给 libuv，**避免每次读 malloc**。普通协议路径有一次拷贝（构造不可变 `bytes`）；若实现 `BufferedProtocol`，libuv 可直接读进协议缓冲，实现真正零拷贝——这是高吞吐服务推荐 `BufferedProtocol` 的原因。

### 2.2 写路径快路径对比

asyncio（`selector_events.py`）：`transport.write(data)` 先试 `sock.send(data)`；内核缓冲满则剩余字节存入 `self._buffer` 并 `loop._add_writer(fd, self._write_ready)`，可写时排空，清空后 `_remove_writer` 注销避免空转。

uvloop（`UVStream._write`，`handles/stream.pyx`）有一个更激进的快路径：先尝试 `uv_try_write()`——**非阻塞同步写，不分配请求、不排队、无回调**。一次写完则零排队零分配完成。只有写不完时才用 `uv_write()` + `uv_write_t` 异步发送，并由 `_StreamWriteContext` 持有缓冲引用，保证异步写完成前内存不被 GC（避免额外拷贝）。

### 2.3 定时器与跨线程唤醒

| 机制 | asyncio | uvloop |
|------|---------|--------|
| 定时器存储 | Python `heapq` 维护 `_scheduled` 堆，`TimerHandle.__lt__` 比较 `_when` | libuv `uv_timer_t` 定时堆（`handles/timer.pyx`），C 回调触发 |
| `call_soon` | 构造纯 Python `Handle`，`_ready.append` | 构造 `cdef class Handle`（`cbhandles.pyx`），`_ready` + 启动 idle |
| 跨线程唤醒 | `_write_to_self()` 向 self-pipe 写 1 字节（write syscall） | `handler_async.send()`（`uv_async_send`，`async_.pyx`） |

asyncio 的 `call_soon_threadsafe()`（`base_events.py`）总成本 = 调用线程一次写 syscall + loop 线程唤醒后一次读 syscall + GIL 移交，高频跨线程时 self-pipe 往返成为瓶颈。

### 2.4 基准数据表

> 数据来自 MagicStack 2016 年公布的 uvloop 基准，**应视为量级参考而非精确保证**，随 Python/libuv 版本、硬件、消息大小、协议实现显著变化。结论以你自己环境的 benchmark 为准。

| 场景 | asyncio | uvloop | 倍率 | 备注 |
|------|---------|--------|------|------|
| TCP echo 吞吐（1KB 消息） | 约 0.5 Gbps | 约 2+ Gbps | 2x 以上 | 消息越大优势越明显 |
| 大消息 TCP echo | — | 接近 Go/Node.js/gevent 水平 | — | 大消息时差距最大 |
| HTTP（uvloop + httptools） | 明显低于 | 约 10 万级 req/s | — | 接近 Go `net/http` |
| 高并发连接网络服务 | 基线 | 2–4x | 2–4x | 连接数越高收益越稳 |
| CPU 密集负载 | 基线 | 几乎无差异 | ~1x | 瓶颈不在 I/O 边界 |

## 3. 性能差异根本原因

第一，**热路径 Python↔C 边界跨越次数被大幅压缩**，这是结构性主因。asyncio 一次网络读至少经历：`selector.select` → 遍历就绪 key → `transport._read_ready` → `sock.recv` → 构造 bytes → `protocol.data_received`，每一步都进出解释器，伴随大量属性查找与临时对象创建。uvloop 把循环迭代、就绪队列处理（`_on_idle`）、传输读写、回调对象创建全部编译成 C，只在 `protocol.data_received` 这一步跨入 Python。

第二，**`with nogil` 包住 `uv_run`**，epoll 阻塞期间释放 GIL，而 asyncio 的 `selector.select` 也会释放 GIL，但其外围的 Python 调度逻辑全程持 GIL。

第三，**回调对象更轻**。asyncio 每个 `Handle._run()` 都要 `self._context.run(callback, *args)`（`events.py`），即一次 contextvars 上下文切换 + 一次 Python 函数调用；海量小回调场景下这部分固定开销占比可观。uvloop 的 `cbhandles.pyx` 是 `cdef class`，路径几乎无解释器开销。

第四，**子系统下沉**。定时器交给 libuv C 定时堆而非 Python `heapq`；跨线程唤醒用 `uv_async_send` 而非 self-pipe write syscall；DNS 走 libuv 线程池 + 数值地址快路径（`dns.pyx`）避免无谓下发；子进程由 libuv 统一处理 `SIGCHLD`（`uv_spawn`），免去 asyncio child watcher 那套复杂度。

第五，也是反向的关键认知：**底层多路复用相同**。libuv 和 asyncio 在 Linux 上都用 epoll，所以差距不来自「内核 I/O 更快」。这也直接推出——**CPU 密集负载下两者无差异**，因为瓶颈在 GIL 下的单线程字节码执行，不在 I/O 边界，uvloop 的全部优化都失效。

## 4. 适用场景建议

### 选 uvloop

- 平台是 Linux / macOS，且生产环境追求网络性能。
- 网络/I-O 密集、高吞吐、高并发连接：API 网关、反向代理、长连接推送服务、高 QPS HTTP 服务。
- 依赖的库都是标准 asyncio 高层用法（aiohttp、asyncpg 等），不触碰 selector loop 私有内部。
- 高吞吐服务建议配合 `BufferedProtocol` 走真正零拷贝读路径，再叠加 `httptools` 等 C 协议解析。

### 选原生 asyncio

- 目标平台含 Windows——**uvloop 完全不支持 Windows**，没有 wheel 也没接 IOCP 后端，必须用 asyncio 的 `ProactorEventLoop`（`SelectorEventLoop` 在 Windows 有 fd 数量限制）。
- CPU 密集为主、I/O 边界不是瓶颈的负载（uvloop 收益甚微）。
- 需要最大兼容性/可调试性，或依赖 selector loop 内部细节的库：直接访问 `loop._selector`、对 `_SelectorSocketTransport` 做 monkeypatch、依赖 `add_reader/add_writer` 边角行为的代码（uvloop 支持这两个接口但不是高性能路径）。
- 开发期排错阶段：原生实现栈贴近文档、报错更直观；uvloop 的栈是 Cython，SSL 走自带 `sslproto.pyx`，调试时栈不同。

### 实务建议

用 `uvloop.install()` 或 `uvloop.run(main())` 一行切换，并保持代码只用标准 asyncio 接口，这样可随时回退，把 uvloop 当纯粹的性能后端。升级后跑全量集成测试再上线，风险集中在「深入 asyncio 私有内部」的依赖库。

## 5. 总结与展望

asyncio 的本质是「就绪事件多路复用 + 回调队列 + 定时器堆」之上的协作式单线程调度器：`_run_once` 是心跳，`Future` 状态机是协程挂起/恢复的同步原语，`_asyncio` C 扩展在热路径消除 Python 开销。uvloop 没有否定这套模型，而是用 Cython + libuv 把同一套语义的**外壳整体下沉到 C**——`uv_run` 长驻、idle 句柄驱动就绪队列、`uv_try_write` 快写、libuv 定时堆与线程池——从而把每轮迭代的 Python↔C 跨越降到接近零。理解两者性能边界的统一钥匙是：**底层 I/O syscall 相同，差距全在边界跨越的密度；一旦负载是 CPU 密集而非 I/O 密集，两者退化为同一条单线程 GIL 曲线。**

展望几条值得关注的趋势：

- asyncio 自身在持续 C 化与减少调度开销。3.11 的结构化并发 `TaskGroup`、`asyncio.timeout()`、PEP 659 零开销异常让 `await` 改用 `SEND` 字节码；3.12 的 Eager Task（`eager_task_factory`）允许立即启动以省去一轮调度。这些优化压缩了 asyncio 与 uvloop 在协程侧的差距,但循环侧的边界跨越差距依然结构性存在。
- 自由线程（PEP 703，no-GIL）会动摇「单线程 loop」这一共同前提。若 GIL 可选移除，多 loop 真并行成为可能,两者都需重新评估在共享数据结构上的锁开销——这可能是下一个分水岭。
- uvloop 的 Windows 缺位短期难改，跨平台部署仍要为两套后端预留切换路径；保持「只用标准 asyncio 接口」的工程纪律,是同时吃到 uvloop 性能与 asyncio 可移植性的最稳做法。

> 注：asyncio 部分的代码路径基于 CPython 3.4→3.12+ 稳定文件归属；uvloop 部分基于 uvloop 0.17–0.21 / libuv 1.x 源码树结构，本工作区无 uvloop 本地 checkout,路径来自源码了解而非实地核对。基准数字为公开数据的量级回忆值,请以自身环境实测为准。