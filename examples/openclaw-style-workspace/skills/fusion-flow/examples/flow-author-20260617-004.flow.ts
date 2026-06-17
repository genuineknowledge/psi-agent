// PRIMITIVES: agent, parallel, session, output
// SCENARIO: 并行深入研究 Python asyncio 和 uvloop 的实现原理与性能差异
// AUTHORED: 2026-06-17 03:13 by Fuclaw authoring mode from intent: "并行深入研究 Python asyncio 和 uvloop 的实现原理与性能差异"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    // === 1. Declare agents ===

    const asyncioResearcher = flow.agent({
      name: "asyncio_researcher",
      system: [
        "你是一位 Python 底层运行时专家。你的任务是深入研究 CPython 标准库 asyncio 的实现原理。",
        "",
        "请输出一份结构化的技术报告（markdown），必须覆盖以下维度：",
        "",
        "## 1. 事件循环（Event Loop）核心架构",
        "- 事件循环的类层次结构（BaseEventLoop → SelectorEventLoop → ProactorEventLoop）",
        "- I/O 多路复用机制：select / epoll / kqueue / IOCP 的选择逻辑",
        "- 事件循环的主循环 `_run_once()` 的执行流程",
        "- 时间轮（scheduled tasks）的数据结构与调度机制",
        "",
        "## 2. 协程与任务调度",
        "- async/await 语法糖如何编译为生成器/协程对象",
        "- Task 的生命周期：创建 → 调度 → 执行 → 完成/取消",
        "- 协程切换的 C 级实现（`_asyncio` C 扩展模块）",
        "- `asyncio.gather()` 和 `asyncio.wait()` 的内部实现",
        "",
        "## 3. Future / Handle 机制",
        "- Future 的状态机（PENDING → CANCELLED / FINISHED）",
        "- Handle 作为回调的封装：`_call_soon`, `_call_later`, `_call_at`",
        "- 回调栈深度控制与递归防护",
        "",
        "## 4. 传输与协议层（Transports & Protocols）",
        "- Transport/Protocol 抽象的设计哲学",
        "- `_SelectorSocketTransport` / `_SelectorDatagramTransport` 实现",
        "- 与事件循环的交互：读/写就绪通知机制",
        "",
        "## 5. 性能瓶颈分析",
        "- GIL 对事件循环的影响（I/O 密集场景）",
        "- 回调开销：Python 函数调用 vs C 扩展调用路径",
        "- `call_soon_threadsafe()` 的跨线程同步开销",
        "- 高并发下的连接管理开销",
        "",
        "## 6. 关键版本演进",
        "- Python 3.4: asyncio 首次引入（yield from）",
        "- Python 3.5-3.6: async/await 语法支持 + C 加速",
        "- Python 3.7-3.8: `asyncio.run()` 高层 API + `asyncio.create_task()`",
        "- Python 3.9-3.11: TaskGroup、to_thread、超时改进",
        "- Python 3.12+: 进一步提升事件循环性能的改动",
        "",
        "每个维度要给出具体的代码路径（如 `asyncio/base_events.py` 中的类名/函数名）和实现细节。",
        "输出纯 markdown，不使用代码块包裹（你本身的输出就是报告内容）。",
      ].join("\n"),
    });

    const uvloopResearcher = flow.agent({
      name: "uvloop_researcher",
      system: [
        "你是一位 Python 底层运行时和网络性能专家。你的任务是深入研究 uvloop（libuv 封装的 asyncio 事件循环替代实现）的实现原理。",
        "",
        "请输出一份结构化的技术报告（markdown），必须覆盖以下维度：",
        "",
        "## 1. libuv 底层架构",
        "- libuv 的事件循环模型：单线程 + 异步 I/O + 回调",
        "- libuv 的核心句柄类型：uv_handle_t 继承体系",
        "- 跨平台 I/O 多路复用抽象：epoll / kqueue / IOCP / event ports",
        "- libuv 的线程池（Thread Pool）机制与用途",
        "",
        "## 2. uvloop 如何替代 asyncio 事件循环",
        "- `uvloop.Loop` 类与 CPython `BaseEventLoop` 的继承关系",
        "- Cython 包装层：Python <→ libuv C API 的桥接",
        "- 事件循环核心 `_run()` 方法的执行流程（与 asyncio 的 `_run_once()` 对比）",
        "",
        "## 3. TCP/UDP 传输层实现",
        "- `UVStream` / `UVTransport` 如何替换 `_SelectorSocketTransport`",
        "- `uv_tcp_t` 和 `uv_udp_t` 的 Python 层封装",
        "- 零拷贝和 Buffer 管理策略",
        "- 连接建立/关闭的完整生命周期",
        "",
        "## 4. 信号、子进程、DNS",
        "- uvloop 的信号处理机制（`uv_signal_t`）",
        "- `uvloop.Loop.subprocess_exec()` 与 libuv 进程管理",
        "- `uv_getaddrinfo()` 异步 DNS 解析（替换阻塞的 `getaddrinfo`）",
        "",
        "## 5. 性能优化的关键技术",
        "- 为什么 uvloop 比 asyncio 快 2-4x（关键路径分析）",
        "- 减少 Python C-API 调用次数：批量操作与内存复用",
        "- libuv 的 I/O 轮询模式（`UV_RUN_ONCE`）vs asyncio 的 selector 轮询",
        "- uvloop 对 `call_soon` / `call_at` 的优化实现",
        "- 基准测试数据（如 uvloop README 中的 TCP echo、HTTP 服务器测试）",
        "- uvloop 对 `asyncio.Queue` / `asyncio.Lock` 等同步原语的加速机制",
        "",
        "## 6. 局限性与适用场景",
        "- Windows 上的 ProactorEventLoop 兼容性",
        "- 不支持的功能/API 列表",
        "- 与第三方 asyncio 库的兼容性风险",
        "- 何时选择 uvloop，何时保留 asyncio 原生事件循环",
        "",
        "每个维度要给出具体的代码路径（如 `uvloop/loop.pyx` 中的类名/函数名）和实现细节。",
        "输出纯 markdown，不使用代码块包裹（你本身的输出就是报告内容）。",
      ].join("\n"),
    });

    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是一位技术文档撰写专家，擅长将多份深度技术报告合并为高质量的综合对比文档。",
        "",
        "你的输入是两份关于 Python asyncio 和 uvloop 实现原理的深度研究报告。请将它们融合为一份完整的对比分析报告，格式如下：",
        "",
        "---",
        "# Python asyncio vs uvloop：实现原理与性能深度对比",
        "",
        "## 摘要（≤ 150 字）",
        "- 一句话概括两者的本质差异",
        "- 核心性能结论",
        "",
        "## 对比总表",
        "| 维度 | asyncio | uvloop | 胜出 |",
        "|------|---------|--------|------|",
        "| 事件循环引擎 | ... | ... | ... |",
        "| I/O 多路复用 | ... | ... | ... |",
        "| 协程调度 | ... | ... | ... |",
        "| TCP 吞吐量 | ... | ... | ... |",
        "| DNS 解析 | ... | ... | ... |",
        "| 子进程管理 | ... | ... | ... |",
        "| 信号处理 | ... | ... | ... |",
        "| 跨平台性 | ... | ... | ... |",
        "| 生态兼容 | ... | ... | ... |",
        "",
        "## 1. 架构差异深度分析",
        "- 事件循环模型对比（附流程图文字描述）",
        "- I/O 多路复用的不同层次",
        "- 协程切换路径对比（Python 层 vs C 层调用链）",
        "",
        "## 2. 关键路径性能对比",
        "- TCP echo 基准测试数据对比表",
        "- HTTP 服务器吞吐量对比",
        "- `call_soon` / `call_at` 调度延迟对比",
        "- 高并发连接（10k+）下的内存与 CPU 开销",
        "",
        "## 3. 性能差异的根本原因",
        "- libuv 相比 select/epoll 原生调用的优势",
        "- Cython/C 扩展 vs 纯 Python 回调链",
        "- 批量化操作减少 Python C-API 边界跨越",
        "",
        "## 4. 适用场景建议",
        "- 推荐使用 uvloop 的场景（≥ 3 个具体案例）",
        "- 推荐保持 asyncio 原生事件循环的场景（≥ 3 个）",
        "- 迁移成本与风险评估",
        "",
        "## 5. 版本兼容性矩阵",
        "- Python 版本支持范围",
        "- 关键依赖版本约束",
        "",
        "## 6. 总结",
        "- 核心结论（≤ 3 条）",
        "- 未来展望（asyncio 3.12+ 的追赶方向 + uvloop 的发展方向）",
        "",
        "要求：",
        "- 每个对比点都要有具体数据/代码路径支撑",
        "- 技术深度足以让有 Python 异步编程经验的开发者获得新认知",
        "- 对比表首列使用 ✅ 标记胜出一方",
        "- 输出纯 markdown，不使用代码块包裹",
      ].join("\n"),
    });

    // === 2. Fan-out: parallel deep research ===

    const [asyncioReport, uvloopReport] = await flow.parallel([
      async () => flow.session(asyncioResearcher, "请深入研究 Python asyncio 标准库的实现原理。"),
      async () => flow.session(uvloopResearcher, "请深入研究 uvloop 的实现原理（基于 libuv）。"),
    ]);

    await save("asyncio_report", asyncioReport);
    await save("uvloop_report", uvloopReport);

    // === 3. Synthesize into comparison report ===

    const final = await flow.session(
      synthesizer,
      "请将以下两份 asyncio 与 uvloop 的深度研究报告融合为一份完整的实现原理与性能对比文档。",
      {
        asyncio_report: asyncioReport,
        uvloop_report: uvloopReport,
      },
    );

    await flow.output("final", final);

    console.log("\n========== Final Report ==========\n");
    console.log(final);
    console.log("\n===================================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
