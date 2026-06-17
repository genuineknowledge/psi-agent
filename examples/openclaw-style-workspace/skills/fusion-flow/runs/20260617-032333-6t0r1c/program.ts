// PRIMITIVES: agent, parallel, session, save, output
// SCENARIO: 并行深入研究 Python asyncio 和 uvloop 的实现原理与性能差异
// AUTHORED: 2026-06-17 03:24 by Fuclaw authoring mode from intent: "并行深入研究 Python asyncio 和 uvloop 的实现原理与性能差异"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    // === 1. Declare agents ===

    const asyncioResearcher = flow.agent({
      name: "asyncio_researcher",
      system: [
        "你是 Python 底层运行时专家。输出一份技术报告（纯 markdown），必须覆盖以下要点。每个要点给出具体代码路径。",
        "",
        "## 1. 事件循环核心架构",
        "- SelectorEventLoop / ProactorEventLoop 继承链",
        "- `_run_once()` 主循环流程：I/O 轮询 → 就绪回调 → 定时任务",
        "- I/O 多路复用选择逻辑：epoll / kqueue / IOCP",
        "",
        "## 2. 协程与任务调度",
        "- async/await → 生成器协程对象的编译过程",
        "- Task 生命周期：创建 → `__step` → 完成/取消",
        "- `_asyncio` C 扩展模块的协程切换路径",
        "",
        "## 3. Future / Handle 机制",
        "- Future 状态机（PENDING/CANCELLED/FINISHED）",
        "- Handle 回调封装：`_call_soon` / `_call_later` / `_call_at`",
        "",
        "## 4. 传输与协议层",
        "- Transport/Protocol 抽象设计",
        "- `_SelectorSocketTransport` 的读/写就绪通知",
        "",
        "## 5. 性能瓶颈",
        "- GIL 对事件循环影响、回调的 Python 调用开销",
        "- `call_soon_threadsafe()` 跨线程同步开销",
        "",
        "## 6. 版本演进",
        "- 3.4→3.12+ 关键改进节点",
      ].join("\n"),
    });

    const uvloopResearcher = flow.agent({
      name: "uvloop_researcher",
      system: [
        "你是 Python 网络性能专家。输出一份技术报告（纯 markdown），必须覆盖以下要点。每个要点给出具体代码路径。",
        "",
        "## 1. libuv 架构",
        "- 单线程事件循环 + 线程池模型",
        "- 跨平台 I/O 多路复用抽象：epoll/kqueue/IOCP",
        "- 核心句柄类型：uv_handle_t 继承体系",
        "",
        "## 2. uvloop 替换 asyncio 事件循环",
        "- `uvloop.Loop` 与 CPython `BaseEventLoop` 的继承关系",
        "- Cython 桥接层：Python ↔ libuv C API",
        "- `_run()` 执行流程 vs asyncio `_run_once()`",
        "",
        "## 3. TCP/UDP 传输层",
        "- `UVTransport` 替换 `_SelectorSocketTransport` 的机制",
        "- `uv_tcp_t` / `uv_udp_t` 的 Python 封装",
        "- 零拷贝与 Buffer 管理",
        "",
        "## 4. 信号/子进程/DNS",
        "- `uv_signal_t` 信号处理、异步 DNS（`uv_getaddrinfo`）",
        "- `subprocess_exec()` 与 libuv 进程管理",
        "",
        "## 5. 性能关键路径优化",
        "- 为什么 uvloop 快 2-4x：减少 Python C-API 边界跨越",
        "- libuv `UV_RUN_ONCE` 轮询 vs asyncio selector 轮询",
        "- `call_soon` / `call_at` 的优化实现",
        "- 基准数据：TCP echo / HTTP 服务器的实际吞吐对比",
        "",
        "## 6. 局限性",
        "- Windows ProactorEventLoop 兼容性",
        "- 不支持的 API 列表、第三方兼容性风险",
        "- uvloop vs asyncio 原生事件循环选择指南",
      ].join("\n"),
    });

    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是技术文档专家。将两份关于 asyncio 和 uvloop 的技术报告融合为一份深度对比文档。",
        "",
        "结构：",
        "## 摘要（≤100字）",
        "## 对比总表（维度/asyncio/uvloop/胜出，用 ✅ 标记）",
        "## 1. 架构差异（事件循环模型+I/O多路复用+协程切换路径）",
        "## 2. 关键路径性能对比（含基准数据表）",
        "## 3. 性能差异根本原因",
        "## 4. 适用场景建议（各 ≥3 个）",
        "## 5. 总结与展望",
        "",
        "要求：每个对比点要有具体数据/代码路径。深度足以让有经验的 Python 异步开发者获得新认知。",
      ].join("\n"),
    });

    // === 2. Fan-out: parallel deep research ===
    const [asyncioReport, uvloopReport] = await flow.parallel([
      async () => flow.session(asyncioResearcher, "请深入研究 Python asyncio 标准库的实现原理，按你 system prompt 中的维度输出完整报告。"),
      async () => flow.session(uvloopResearcher, "请深入研究 uvloop（libuv 封装的 asyncio 事件循环替代）的实现原理，按你 system prompt 中的维度输出完整报告。"),
    ]);

    await save("asyncio_report", asyncioReport);
    await save("uvloop_report", uvloopReport);

    // === 3. Synthesize ===
    const final = await flow.session(
      synthesizer,
      "将以下两份深度研究报告融合为 asyncio vs uvloop 实现原理与性能差异对比文档。",
      { asyncio_report: asyncioReport, uvloop_report: uvloopReport },
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
