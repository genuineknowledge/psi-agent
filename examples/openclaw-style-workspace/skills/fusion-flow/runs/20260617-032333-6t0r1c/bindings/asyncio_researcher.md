# Python asyncio 标准库实现原理技术报告

> 说明：以下代码路径均指 CPython 源码树（`cpython/`）中的稳定文件路径。具体行号随版本浮动，但文件归属与函数名长期稳定。报告基于 CPython 3.4 → 3.12+ 的实现，关键差异在第 6 节标注。

---

## 1. 事件循环核心架构

### 1.1 继承链

asyncio 的事件循环采用「抽象接口 → 通用实现 → I/O 后端特化」三层结构：

```
AbstractEventLoop            Lib/asyncio/events.py        # 纯接口定义
        ▲
BaseEventLoop                Lib/asyncio/base_events.py   # 调度核心：_run_once / 定时器 / call_soon
        ▲
   ┌────┴───────────────────────────────┐
BaseSelectorEventLoop                BaseProactorEventLoop
Lib/asyncio/selector_events.py       Lib/asyncio/proactor_events.py
        ▲                                    ▲
_UnixSelectorEventLoop               ProactorEventLoop
Lib/asyncio/unix_events.py           Lib/asyncio/windows_events.py
        ▲
_WindowsSelectorEventLoop
Lib/asyncio/windows_events.py
```

- `SelectorEventLoop` 是平台别名：Unix 上指向 `_UnixSelectorEventLoop`，Windows 上指向 `_WindowsSelectorEventLoop`（见 `Lib/asyncio/unix_events.py` 与 `Lib/asyncio/windows_events.py` 末尾的 `SelectorEventLoop = ...`）。
- `BaseEventLoop` 不直接做 I/O，它只持有 `self._selector`（由子类注入）并维护两个调度容器：
  - `self._ready`：`collections.deque`，FIFO 的就绪回调队列。
  - `self._scheduled`：`heapq` 维护的 `TimerHandle` 最小堆，按 `_when` 排序。

### 1.2 `_run_once()` 主循环流程

核心在 `BaseEventLoop._run_once()`（`Lib/asyncio/base_events.py`），单次迭代严格按三阶段执行：

```python
def _run_once(self):
    # 阶段 0：计算 select 超时
    if self._ready or self._stopping:
        timeout = 0                       # 有就绪回调则不阻塞
    elif self._scheduled:
        when = self._scheduled[0]._when   # 最近定时任务
        timeout = min(max(0, when - self.time()), MAXIMUM_SELECT_TIMEOUT)

    # 阶段 1：I/O 轮询
    event_list = self._selector.select(timeout)
    self._process_events(event_list)      # 将就绪 fd 的回调塞入 _ready

    # 阶段 2：到期定时任务出堆
    end_time = self.time() + self._clock_resolution
    while self._scheduled:
        handle = self._scheduled[0]
        if handle._when >= end_time:
            break
        handle = heapq.heappop(self._scheduled)
        handle._scheduled = False
        self._ready.append(handle)

    # 阶段 3：执行就绪回调（快照长度，避免本轮处理新追加的回调）
    ntodo = len(self._ready)
    for i in range(ntodo):
        handle = self._ready.popleft()
        if handle._cancelled:
            continue
        handle._run()
```

关键设计点：
- 用 `ntodo` 做长度快照，本轮新 `call_soon` 进来的回调留到下一轮，保证公平性与可终止性。
- `_clock_resolution` 用来补偿时钟精度，避免定时任务因纳秒级误差被延后一整轮。
- `select(timeout)` 是循环唯一的阻塞点；只要 `_ready` 非空，`timeout=0` 退化为纯轮询。

### 1.3 I/O 多路复用选择逻辑

selector 后端在 `Lib/selectors.py`，按平台能力降级：

```
DefaultSelector  →  EpollSelector   (Linux,   epoll_wait)
                    KqueueSelector  (BSD/mac,  kqueue/kevent)
                    DevpollSelector (Solaris)
                    PollSelector    (POSIX poll)
                    SelectSelector  (兜底 select)
```

- `BaseSelectorEventLoop._process_events()`（`Lib/asyncio/selector_events.py`）遍历 `event_list`，从 `SelectorKey.data` 取出 `(reader, writer)` 两个 `Handle`，按 `EVENT_READ` / `EVENT_WRITE` 决定调用哪个，最终通过 `_add_callback()` 追加到 `_ready`。
- IOCP **不在** `selectors` 体系内。`ProactorEventLoop` 使用 `Modules/overlapped.c`（`_overlapped` C 扩展）封装 Windows 重叠 I/O，由 `IocpProactor`（`Lib/asyncio/windows_events.py`）调用 `GetQueuedCompletionStatus` 取完成事件。这是 selector（就绪通知 / readiness）与 proactor（完成通知 / completion）两种模型的本质区别。

---

## 2. 协程与任务调度

### 2.1 async/await 的编译过程

- `async def` 在编译期（`Python/compile.c`）给 code object 打上 `CO_COROUTINE` 标志。调用该函数不会执行函数体，而是构造一个协程对象 `PyCoro_Type`（`Objects/genobject.c`，与生成器共享底层帧机）。
- `await expr` 的字节码：
  - 先 `GET_AWAITABLE`（`Python/ceval.c` 中实现），把对象转为 awaitable（调用 `__await__`）。
  - 3.10 及以前：进入 `YIELD_FROM` 循环委托。
  - 3.11+：改用 `SEND` + `YIELD_VALUE` 配合零开销帧（PEP 659 适配性优化），委托效率显著提升。
- 协程的驱动统一走生成器协议：`gen_send_ex()` / `_PyGen_Send()`（`Objects/genobject.c`）。`cr_await` 字段记录当前 `await` 的下游对象，用于 `asyncio` 的调试与栈回溯。

### 2.2 Task 生命周期

`Task` 有两份实现：纯 Python 版在 `Lib/asyncio/tasks.py`，C 加速版 `TaskObj` 在 `Modules/_asynciomodule.c`（导入时若 `_asyncio` 可用则覆盖 Python 版）。生命周期：

```
create_task(coro)
   └─ Task.__init__：loop.call_soon(self.__step) 安排首次驱动
        │
        ▼
   __step()  ── coro.send(None) / coro.throw(exc)
        │
        ├─ 协程 yield 一个 Future
        │     → fut.add_done_callback(self.__wakeup)
        │     → self._fut_waiter = fut   （挂起，等待唤醒）
        │
        ├─ 协程 yield None（裸 yield）
        │     → loop.call_soon(self.__step)（让出一轮后继续）
        │
        ├─ raise StopIteration(value)
        │     → super().set_result(value)        FINISHED
        │
        ├─ raise CancelledError
        │     → super().cancel() / set_cancelled  CANCELLED
        │
        └─ raise 其它异常
              → super().set_exception(exc)        FINISHED
```

- `__wakeup(fut)`：被 `_fut_waiter` 的完成回调触发，取出结果（或异常）后再次调用 `__step`，形成「挂起 → 唤醒 → 续跑」闭环。
- `cancel()`：若有 `_fut_waiter` 则取消它；否则设置 `_must_cancel`，下次 `__step` 时向协程 `throw(CancelledError)`。

### 2.3 `_asyncio` C 扩展的协程切换路径

C 加速版的核心是 `task_step` → `task_step_impl`（`Modules/_asynciomodule.c`）。它绕开 Python 层的 `__step`，直接：

1. 用 `PyIter_Send()` / `_PyGen_Send()` 向协程发送值（或 `gen_throw` 注入异常），这是 C 级别的协程切入。
2. 根据返回值类型分发：是 Future 则调 `future_add_done_callback`（同模块内的 C 函数）注册 `task_wakeup`；是 `StopIteration` 则 `future_set_result`。
3. `task_wakeup`（`Modules/_asynciomodule.c`）在 awaited future 完成时回调，重新进入 `task_step_impl`。

整条切换路径（send → 分发 → 注册回调 → wakeup）全程在 C 层完成，避免了 Python 字节码解释和方法查找开销，这是 3.6+ 协程吞吐提升的主因。

---

## 3. Future / Handle 机制

### 3.1 Future 状态机

`Future` 实现于 `Lib/asyncio/futures.py`，C 版 `FutureObj` 在 `Modules/_asynciomodule.c`。状态字段 `_state` 三态：

```
        set_result() / set_exception()
PENDING ──────────────────────────────► FINISHED
   │
   │ cancel()
   ▼
CANCELLED
```

- 常量定义：`_PENDING = 'PENDING'`、`_CANCELLED = 'CANCELLED'`、`_FINISHED = 'FINISHED'`（`Lib/asyncio/futures.py`）。
- `set_result` / `set_exception` 要求当前必须是 `PENDING`，否则抛 `InvalidStateError`；完成后调用 `__schedule_callbacks()`，把所有 `add_done_callback` 注册的回调通过 `loop.call_soon(callback, self, context=ctx)` 投递。
- 回调列表元素是 `(callback, context)` 二元组，绑定注册时刻的 `contextvars.Context`（3.7+）。
- `Task` 继承 `Future`，所以 Task 复用整套状态机；`cancel()` 在 Task 层被重写以处理协程注入。

### 3.2 Handle 回调封装

`Handle` 与 `TimerHandle` 定义在 `Lib/asyncio/events.py`（同样有 `_asynciomodule.c` 加速）：

```python
class Handle:
    __slots__ = ('_callback', '_args', '_cancelled', '_loop',
                 '_context', ...)
    def _run(self):
        self._context.run(self._callback, *self._args)  # 在捕获的上下文中执行
```

- `loop.call_soon(cb, *args)`（`Lib/asyncio/base_events.py`）：构造 `Handle`，`self._ready.append(handle)`，立即可在本轮或下轮 `_run_once` 执行。
- `loop.call_later(delay, cb, *args)`：转调 `call_at(self.time() + delay, ...)`。
- `loop.call_at(when, cb, *args)`：构造 `TimerHandle(when, cb, args, self)`，`heapq.heappush(self._scheduled, timer)`，`timer._scheduled = True`。
- `TimerHandle` 实现 `__lt__` / `__le__`（比较 `_when`）以支持堆排序；取消时只置 `_cancelled = True`，由 `_run_once` 阶段 3 跳过，惰性清理。

---

## 4. 传输与协议层

### 4.1 Transport / Protocol 抽象设计

asyncio 借鉴 Twisted，将「字节搬运」与「业务逻辑」解耦：

- `Transport`（`Lib/asyncio/transports.py`）：面向应用提供 `write()` / `writelines()` / `close()` / `pause_reading()` 等，封装底层 socket/pipe。
- `Protocol`（`Lib/asyncio/protocols.py`）：由用户实现回调，`connection_made(transport)` / `data_received(data)` / `eof_received()` / `connection_lost(exc)`。
- 二者通过 `transport` 持有 `protocol` 引用单向耦合：Transport 在 I/O 就绪时主动回调 Protocol 方法，Protocol 通过持有的 transport 句柄写数据。流控（背压）通过 `pause_writing()` / `resume_writing()` 在两层间协商。

### 4.2 `_SelectorSocketTransport` 读/写就绪通知

实现于 `Lib/asyncio/selector_events.py`：

读路径：
- 构造时 `loop._add_reader(self._sock_fd, self._read_ready)` 把读回调注册进 selector。
- I/O 可读时 `_run_once` 触发 `_read_ready`：
  - 普通协议走 `_read_ready__data_received`：`sock.recv()` → `protocol.data_received(data)`；返回空字节表示 EOF → 调 `protocol.eof_received()`。
  - 缓冲协议（`BufferedProtocol`，3.7+）走 `_read_ready__get_buffer`：`protocol.get_buffer()` 拿到可写缓冲，`sock.recv_into()` 零拷贝填充，再 `buffer_updated(nbytes)`。

写路径：
- `transport.write(data)` 先尝试 `sock.send(data)` 立即发送；
- 若内核缓冲满（部分发送或 `BlockingIOError`），把剩余字节存入 `self._buffer`（`bytearray`/`collections.deque`），并 `loop._add_writer(fd, self._write_ready)` 注册写就绪回调；
- fd 可写时触发 `_write_ready`：继续 `sock.send` 排空 `_buffer`，清空后 `loop._remove_writer(fd)` 注销写监听，避免空转。
- 流控水位：缓冲超过 `_high_water` 调 `protocol.pause_writing()`，回落到 `_low_water` 调 `resume_writing()`（`set_write_buffer_limits()` 配置）。

---

## 5. 性能瓶颈

### 5.1 GIL 与回调调用开销

- 事件循环本质单线程：`_run_once` 顺序执行 `_ready` 中所有回调，全程持有 GIL。任何一个 CPU 密集或阻塞的回调都会卡住整个循环（典型反模式：在协程里跑同步 `requests` 或大循环）。
- 多线程各自跑独立 loop 也无法并行执行 Python 字节码——它们仍争抢同一个 GIL，I/O 等待期间才会释放。
- 回调开销：每个 `Handle._run()` 都要 `self._context.run(callback, *args)`（`Lib/asyncio/events.py`），即一次 contextvars 上下文切换 + 一次 Python 函数调用。海量小回调场景下，这部分固定开销占比可观。C 加速的 `Future`/`Task`（`Modules/_asynciomodule.c`）正是为压低这块成本而生，但纯 Python 用户回调本身无法被加速。

### 5.2 `call_soon_threadsafe()` 跨线程同步开销

`BaseEventLoop.call_soon_threadsafe()`（`Lib/asyncio/base_events.py`）流程：

```python
def call_soon_threadsafe(self, callback, *args, context=None):
    handle = self._call_soon(callback, args, context)  # 入队 _ready
    self._write_to_self()                              # 唤醒阻塞中的 selector
    return handle
```

- 关键是 `_write_to_self()`：向 self-pipe（`_make_self_pipe` 建立的 socketpair / 自连接 socket）写一个字节。这是一次 `send`/`write` 系统调用。
- loop 线程此刻可能正阻塞在 `selector.select(timeout)`，self-pipe 的可读事件令其立即返回，`_read_from_self` 把那个字节读掉，从而尽快处理新入队的回调。
- 跨线程总成本 = 调用线程的一次写 syscall + loop 线程被唤醒后的一次读 syscall + GIL 在两线程间的移交。高频跨线程投递时，self-pipe 的 syscall 往返会成为瓶颈，这也是「尽量把工作留在 loop 线程内」的原因。

---

## 6. 版本演进（3.4 → 3.12+）

| 版本 | 关键节点 | 相关路径 / 机制 |
|------|----------|----------------|
| **3.4** | PEP 3156：asyncio 诞生。`@asyncio.coroutine` + `yield from` 风格，Future/Task/Transport/Protocol 雏形 | `Lib/asyncio/*` 初版 |
| **3.5** | PEP 492：原生 `async def` / `await`，协程对象与生成器在类型层面分离 | `Objects/genobject.c` 引入 `PyCoro_Type` |
| **3.6** | 异步生成器（PEP 525）、异步推导式；**`_asyncio` C 加速模块落地**，Future/Task 大幅提速 | `Modules/_asynciomodule.c` |
| **3.7** | `asyncio.run()`、`get_running_loop()`；PEP 567 contextvars 接入 Handle/Task；`current_task`/`all_tasks` 改为模块函数；`BufferedProtocol` | `Lib/asyncio/runners.py`、`events.py` |
| **3.8** | Windows 默认改用 `ProactorEventLoop`；Happy Eyeballs；命名 Task；读回调拆分为 `_read_ready__data_received` / `__get_buffer` | `windows_events.py`、`selector_events.py` |
| **3.9** | `asyncio.to_thread()`（线程池桥接）；默认 executor 优雅关闭；`PidfdChildWatcher` | `Lib/asyncio/threads.py`、`unix_events.py` |
| **3.10** | 全面弃用各 API 的 `loop=` 显式参数，强制使用运行中 loop | 全 `Lib/asyncio/*` |
| **3.11** | **结构化并发 `TaskGroup`**、`asyncio.timeout()`、ExceptionGroup；CPython 解释器零开销异常 + 适配性指令（PEP 659），`await` 改用 `SEND` 字节码，协程切换显著加速 | `Lib/asyncio/taskgroups.py`、`timeouts.py`、`Python/ceval.c` |
| **3.12** | **Eager Task**：`asyncio.eager_task_factory` / `create_task` 支持立即启动以省去一轮调度；`current_task` 等进一步 C 化；整体性能优化 | `Lib/asyncio/tasks.py`、`Modules/_asynciomodule.c` |
| **3.13（前瞻）** | 自由线程（no-GIL）实验构建对单线程 loop 模型的影响；`asyncio` REPL；Task 自省增强 | 受 PEP 703 影响的运行时 |

---

## 总结

asyncio 的本质是一个建立在「就绪事件多路复用 + 回调队列 + 定时器堆」之上的协作式单线程调度器：`_run_once` 是心跳，`Future` 状态机是协程挂起/恢复的同步原语，`Task.__step`/`__wakeup` 把协程的 `send`/`throw` 编织成可恢复的执行流，`_asyncio` C 扩展则在热路径上消除 Python 层开销。理解性能边界的关键，是认清「整个循环跑在一个 GIL 下的单线程里」——任何阻塞回调、过细的回调粒度、或高频跨线程 `call_soon_threadsafe` 的 self-pipe 唤醒，都会直接转化为延迟。

如果需要，我可以进一步针对其中某一节（例如 `task_step_impl` 的 C 源码逐行走读，或 `_SelectorSocketTransport` 的完整读写状态机）展开深入分析。