# SSE 实时通信延迟分析

## 1. 协议机制对延迟的影响
- 单向推送 + HTTP 长连接：省去重复握手，连接复用使推送延迟趋近 RTT。
- text/event-stream：纯文本逐行解析，开销极低（< 1ms）。
- 自动重连 (Last-Event-ID)：断线后默认重连间隔 ~3s，是延迟尖峰主因。

## 2. 延迟数据
| 指标 | 典型值 | 说明 |
|---|---|---|
| 连接建立 | 1×RTT + TLS（~50–150ms） | 同 HTTP |
| 端到端推送 | ~RTT + 1–5ms | 接近网络下限 |
| 1 条/秒 | p50 ≈ RTT | 稳定 |
| 100 条/秒 | p99 略升（缓冲/解析） | 仍 < 20ms（局域） |
| vs 长轮询 | 长轮询每条多 1×RTT 重连开销 | SSE 明显更低 |

浏览器限制：HTTP/1.1 下同域 ≤6 连接（Chrome 源码 kMaxConnectionsPerGroup=6）；多标签页易耗尽。HTTP/2 多路复用可达上百，基本解除该限制。

数据来源：MDN EventSource 文档、Chromium 网络栈源码、Fastly/Cloudflare SSE 实践博客（具体数字随网络环境波动，未做独立基准测试，仅为量级参考）。

## 3. 实时场景适配
- AI token 流式：最佳场景，逐 token 推送，延迟 ≈ 模型生成间隔，OpenAI/Anthropic 均用 SSE。
- 通知推送：秒级要求轻松满足。
- 数据看板：多数据源在 HTTP/1.1 受 6 连接限制，应走 HTTP/2 或合并为单流。
- 直播弹幕：可行但单向，高并发广播 (>10k) 时服务端连接保持成本高，瓶颈在扇出与内存，非延迟。

## 4. 协议对比
- vs WebSocket：单向场景下 SSE 实现简单、走标准 HTTP、自动重连内置、对代理/CDN 友好。
- vs HTTP/2 Server Push：后者已被主流浏览器弃用，SSE 是服务端推送实际首选。
- HTTP/3 (QUIC)：消除队头阻塞、0-RTT 重连，弱网下 SSE 重连与抖动改善明显。

## 5. 最佳实践
- 致命点：Nginx `proxy_buffering on` 会缓冲整个流→延迟飙升。必须设 `proxy_buffering off`、关闭 gzip、`X-Accel-Buffering: no`。
- 重连：服务端发 `id:`，客户端断线自动带 `Last-Event-ID` 续传；用 `retry:` 调重连间隔避免风暴。

> 注：表中数字为公开资料量级估计，精确值需按实际链路基准测试。