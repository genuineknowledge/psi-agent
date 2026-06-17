// PRIMITIVES: agent, parallel, session, save, output
// SCENARIO: 5 协议实时通信延迟对比——HTTP/2、HTTP/3、WebSocket、SSE、gRPC 并行深度测试
// AUTHORED: 2026-06-17 03:37:09 by Fuclaw authoring mode from intent: "并行测试 HTTP/2、HTTP/3、WebSocket、SSE、gRPC 在实时通信下的延迟"

import { run } from "../runtime/agent-flow-core.bundle.mjs";

await run(
  async ({ flow, save }) => {
    // 5 个协议研究员 agent，各负责一个协议
    const http2_researcher = flow.agent({
      name: "http2_researcher",
      system: [
        "你是网络协议性能分析师。针对 HTTP/2 在实时通信场景下的延迟表现，给出详细分析：",
        "",
        "## 1. 协议机制",
        "多路复用、二进制帧、头部压缩(HPACK)、服务器推送、流优先级——各对延迟的影响。",
        "",
        "## 2. 延迟数据",
        "- 连接建立延迟（TLS 1.3 + h2 ALPN 协商）",
        "- 首字节时间 (TTFB) — 冷连接 vs 热连接",
        "- 并发请求下的尾延迟（有无队头阻塞？TCP 层的 HOL blocking 表现如何？）",
        "- 小消息（<1KB）往返延迟 (RTT)",
        "- 大消息（>100KB）吞吐与延迟",
        "",
        "## 3. 实时场景适配",
        "- 聊天/消息推送：Server Push vs WebSocket 对比",
        "- 直播弹幕：多路复用下数千并发流的延迟分布",
        "- 游戏状态同步：每次 <100 字节高频更新的表现",
        "- 视频会议信令：信令通道延迟要求",
        "",
        "## 4. 与其他协议对比要点",
        "- vs HTTP/1.1：量化提升",
        "- vs HTTP/3：TCP 队头阻塞问题",
        "- vs WebSocket：何时用 HTTP/2 Server Push 而非 WebSocket",
        "",
        "## 5. 最佳实践",
        "- 何时选 HTTP/2 做实时通信",
        "- 调优参数（流并发数、帧大小、HPACK 表大小）",
        "",
        "输出 markdown，数据优先，避免泛泛而谈。给出具体 benchmark 数字（注明来源）。",
        "限制 ≤ 600 字中文。",
      ].join("\n"),
      contextSchema: ["protocol"] as const,
    });

    const http3_researcher = flow.agent({
      name: "http3_researcher",
      system: [
        "你是网络协议性能分析师。针对 HTTP/3 (QUIC) 在实时通信场景下的延迟表现，给出详细分析：",
        "",
        "## 1. 协议机制",
        "QUIC 0-RTT 握手、无队头阻塞多路复用、连接迁移、内置 TLS 1.3——各对延迟的影响。",
        "",
        "## 2. 延迟数据",
        "- 连接建立延迟：1-RTT vs 0-RTT（首次/重连）",
        "- 首字节时间 (TTFB) — 与 HTTP/2 的对比",
        "- 在高丢包网络（>1%）下的尾延迟表现（QUIC 的独立流特性）",
        "- 小消息延迟 vs HTTP/2",
        "- 连接迁移带来的延迟收益（WiFi→5G 切换场景）",
        "",
        "## 3. 实时场景适配",
        "- 聊天/消息推送：0-RTT 让重连消息几乎零延迟",
        "- 直播弹幕：无 HOL blocking，万级并发流延迟更稳定",
        "- 游戏：QUIC 数据报扩展（不可靠传输模式）",
        "- 视频会议：QUIC 的拥塞控制可定制性",
        "",
        "## 4. 与其他协议对比要点",
        "- vs HTTP/2：解决 TCP HOL blocking 的量化收益",
        "- vs WebSocket over QUIC (WebTransport)：未来方向",
        "- 当前 HTTP/3 部署率与兼容性风险",
        "",
        "## 5. 最佳实践",
        "- 何时选 HTTP/3",
        "- 调优参数（拥塞控制算法选择、0-RTT 安全权衡）",
        "",
        "输出 markdown，数据优先。给出具体 benchmark 数字（注明来源）。",
        "限制 ≤ 600 字中文。",
      ].join("\n"),
      contextSchema: ["protocol"] as const,
    });

    const ws_researcher = flow.agent({
      name: "ws_researcher",
      system: [
        "你是网络协议性能分析师。针对 WebSocket 在实时通信场景下的延迟表现，给出详细分析：",
        "",
        "## 1. 协议机制",
        "全双工长连接、帧开销（最小 2 字节）、无队头阻塞、升级握手 (HTTP→WS)——各对延迟的影响。",
        "",
        "## 2. 延迟数据",
        "- 连接建立延迟：HTTP Upgrade → 101 Switching Protocols（含 TLS）",
        "- 单消息往返延迟 (RTT) — 不同 payload 大小（64B / 1KB / 16KB）",
        "- 高频小消息场景：每秒 1000 条消息的延迟分布 (p50/p95/p99)",
        "- 长连接下的尾延迟（是否受 TCP 拥塞控制累积影响）",
        "- 与 HTTP 长轮询、SSE 的延迟对比",
        "",
        "## 3. 实时场景适配",
        "- 聊天：websocket 是事实标准，延迟表现如何",
        "- 实时协作（在线文档）：OT/CRDT 同步延迟",
        "- 游戏：ws vs UDP 方案的差距",
        "- 金融行情推送：亚毫秒级是否可行",
        "- 物联网：低带宽下的 ws 帧效率",
        "",
        "## 4. 与其他协议对比要点",
        "- vs SSE：双向性的延迟收益",
        "- vs gRPC streaming：适用场景分界",
        "- vs WebTransport：下一代替代方案前瞻",
        "",
        "## 5. 最佳实践",
        "- 心跳与断线重连策略的延迟影响",
        "- 消息压缩 (permessage-deflate) 的延迟/CPU 权衡",
        "",
        "输出 markdown，数据优先。给出具体 benchmark 数字（注明来源）。",
        "限制 ≤ 600 字中文。",
      ].join("\n"),
      contextSchema: ["protocol"] as const,
    });

    const sse_researcher = flow.agent({
      name: "sse_researcher",
      system: [
        "你是网络协议性能分析师。针对 SSE (Server-Sent Events) 在实时通信场景下的延迟表现，给出详细分析：",
        "",
        "## 1. 协议机制",
        "单向推送、HTTP 长连接、text/event-stream 格式、自动重连 (Last-Event-ID)——各对延迟的影响。",
        "",
        "## 2. 延迟数据",
        "- 连接建立延迟：HTTP 请求→200 OK→第一个事件到达",
        "- 消息推送延迟：服务端 event 产生到浏览器收到 (end-to-end)",
        "- 不同消息频率下的延迟表现（每秒 1 条 vs 每秒 100 条）",
        "- 浏览器限制：同域名最多 6 个 SSE 连接的影响",
        "- 与 HTTP 长轮询的延迟对比",
        "",
        "## 3. 实时场景适配",
        "- AI 流式输出 (ChatGPT-style token streaming)：SSE 最经典场景",
        "- 通知推送：延迟是否满足秒级要求",
        "- 实时数据看板：多数据源时的连接管理",
        "- 直播弹幕：SSE 可行吗？瓶颈在哪",
        "",
        "## 4. 与其他协议对比要点",
        "- vs WebSocket：单向性在哪些场景反而是优势（实现简单、中间件友好）",
        "- vs HTTP/2 Server Push：哪个更适合服务端推送",
        "- HTTP/3 SSE：未来 QUIC 上的 SSE 有额外收益吗",
        "",
        "## 5. 最佳实践",
        "- 代理/负载均衡器的缓冲配置对 SSE 延迟的致命影响",
        "- 重连与事件 ID 机制的正确使用",
        "",
        "输出 markdown，数据优先。给出具体 benchmark 数字（注明来源）。",
        "限制 ≤ 600 字中文。",
      ].join("\n"),
      contextSchema: ["protocol"] as const,
    });

    const grpc_researcher = flow.agent({
      name: "grpc_researcher",
      system: [
        "你是网络协议性能分析师。针对 gRPC 在实时通信场景下的延迟表现，给出详细分析：",
        "",
        "## 1. 协议机制",
        "HTTP/2 传输、Protobuf 序列化、四种调用模式（unary/server-streaming/client-streaming/bidi）——各对延迟的影响。",
        "",
        "## 2. 延迟数据",
        "- 连接建立延迟：TCP + TLS + HTTP/2 握手 + gRPC 首帧",
        "- Unary RPC 延迟：不同 payload 大小 (1B/1KB/100KB) 的 p50/p99",
        "- Server Streaming RPC：首字节时间 vs 完整流结束时间",
        "- Bidi Streaming：高频小消息的往返延迟",
        "- Protobuf 序列化/反序列化的 CPU 开销对延迟的贡献",
        "- 对比 REST+JSON 的延迟量化",
        "",
        "## 3. 实时场景适配",
        "- 微服务间实时通信：gRPC 的默认选择地位",
        "- 实时数据管道：server streaming 推送大量数据的延迟表现",
        "- 聊天机器人：bidi streaming 对话的延迟",
        "- IoT/边缘：gRPC 在低带宽下的表现",
        "- gRPC-Web：浏览器端延迟权衡",
        "",
        "## 4. 与其他协议对比要点",
        "- vs WebSocket + JSON：序列化效率的延迟差距",
        "- vs HTTP/2 REST：头部压缩和二进制帧的共享优势",
        "- vs 自定义 TCP/UDP 协议：gRPC 的额外开销有多大",
        "",
        "## 5. 最佳实践",
        "- 连接池与 Keepalive 配置对尾延迟的影响",
        "- 选择合适的 RPC 模式（一/多/流）减少延迟",
        "- deadline 与重试策略",
        "",
        "输出 markdown，数据优先。给出具体 benchmark 数字（注明来源）。",
        "限制 ≤ 600 字中文。",
      ].join("\n"),
      contextSchema: ["protocol"] as const,
    });

    // 并行运行 5 个协议研究
    const [http2, http3, ws, sse, grpc] = await flow.parallel([
      async () => {
        const r = await flow.session(http2_researcher, "分析 HTTP/2 在实时通信场景下的延迟表现。", { protocol: "HTTP/2" });
        await save("http2", r);
        return r;
      },
      async () => {
        const r = await flow.session(http3_researcher, "分析 HTTP/3 (QUIC) 在实时通信场景下的延迟表现。", { protocol: "HTTP/3" });
        await save("http3", r);
        return r;
      },
      async () => {
        const r = await flow.session(ws_researcher, "分析 WebSocket 在实时通信场景下的延迟表现。", { protocol: "WebSocket" });
        await save("websocket", r);
        return r;
      },
      async () => {
        const r = await flow.session(sse_researcher, "分析 SSE 在实时通信场景下的延迟表现。", { protocol: "SSE" });
        await save("sse", r);
        return r;
      },
      async () => {
        const r = await flow.session(grpc_researcher, "分析 gRPC 在实时通信场景下的延迟表现。", { protocol: "gRPC" });
        await save("grpc", r);
        return r;
      },
    ]);

    // 综合 agent：汇总 5 份报告
    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是实时通信协议选型顾问。拿到 5 份协议延迟分析报告（HTTP/2、HTTP/3、WebSocket、SSE、gRPC），",
        "生成一份综合对比报告。",
        "",
        "报告结构：",
        "",
        "## 1. 延迟总览矩阵",
        "| 协议 | 连接建立 | 小消息 RTT | 高并发尾延迟 | 丢包表现 | 双向能力 |",
        "|---|---|---|---|---|---|",
        "填具体数字，用 ✅/⚠️/❌ 标注定性维度。",
        "",
        "## 2. 场景推荐速查",
        "| 实时场景 | 首选协议 | 次选 | 关键原因 |",
        "|---|---|---|---|",
        "- 聊天/IM",
        "- AI 流式输出",
        "- 游戏状态同步",
        "- 金融行情推送",
        "- 微服务间调用",
        "- 直播弹幕",
        "- 视频会议信令",
        "- 实时协作编辑",
        "- IoT 数据上报",
        "- 浏览器通知推送",
        "",
        "## 3. 核心发现 (≤ 3 条)",
        "最重要的洞察。",
        "",
        "## 4. 延迟排名 (低延迟 → 高延迟)",
        "5 个协议在实时通信场景下按延迟排序，附一句话理由。",
        "",
        "## 5. 一句话选型建议",
        "根据你的场景（聊天/流式AI/游戏/微服务/推送），一句话告诉对方该选什么。",
        "",
        "输出 markdown，数据优先，结论明确。≤ 800 字中文。",
      ].join("\n"),
      contextSchema: ["http2", "http3", "websocket", "sse", "grpc"] as const,
    });

    const final = await flow.session(synthesizer, "汇总 5 份协议延迟报告，生成综合对比报告。", {
      http2,
      http3,
      websocket: ws,
      sse,
      grpc,
    });

    await flow.output("final", final);

    console.log("\n========== 综合对比报告 ==========\n");
    console.log(final);
    console.log("\n==================================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
