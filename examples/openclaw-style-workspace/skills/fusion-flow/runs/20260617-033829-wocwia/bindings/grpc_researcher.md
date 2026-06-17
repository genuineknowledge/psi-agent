# gRPC 实时延迟分析

## 1. 机制对延迟的影响
HTTP/2 多路复用消除队头阻塞（连接层仍受 TCP 影响）；Protobuf 二进制编码体积约为 JSON 的 30-50%，解析更快。四模式中 unary 延迟最低；bidi 复用单连接，省去重复握手。

## 2. 延迟数据（参考量级，环境强相关）
| 项 | 数值 |
|---|---|
| 连接建立 (TCP+TLS1.3+H2) | ~1-2 RTT，局域网 1-3ms |
| Unary 1B p50/p99 | ~0.3/1ms（同机房，gRPC 官方基准 ghz/qps） |
| Unary 1KB | ~0.4/1.5ms |
| Unary 100KB | ~1-3/8ms（带宽主导） |
| Server streaming 首字节 | ≈单 RPC RTT；后续帧仅传输耗时 |
| Bidi 高频小消息 RTT | 同机房 0.2-0.5ms（连接复用） |
| Protobuf 序列化 1KB | ~1-5µs，对总延迟贡献 <5% |

对比 REST+JSON：同载荷 gRPC 通常快 20-60%，主因序列化+二进制帧（来源：Google gRPC benchmarks、各厂商如 DreamFactory/YugabyteDB 公开测试，数值随硬件波动，需自测）。

## 3. 实时场景
- 微服务内部通信：低延迟+强类型，默认首选。
- 实时数据管道：server streaming 推送，首字节快、吞吐高。
- 聊天机器人：bidi 维持长连接，对话往返延迟低。
- IoT/边缘：Protobuf 省带宽，弱网下优于 JSON；但 HTTP/2 在高丢包时不及 QUIC。
- gRPC-Web：需代理转译，牺牲流式能力，浏览器端延迟略增。

## 4. 协议对比
- vs WebSocket+JSON：序列化更省、强 schema，延迟更稳。
- vs HTTP/2 REST：共享头部压缩与二进制帧，gRPC 额外赢在 Protobuf。
- vs 自定义 TCP/UDP：gRPC 多 HTTP/2+TLS 开销（每调用约几十µs~ms 级），换取通用性与可维护性。

## 5. 最佳实践
- 连接池 + Keepalive，避免重连尾延迟。
- 按场景选模式：单次用 unary，持续推用 streaming。
- 设置 deadline，配合指数退避重试，防尾延迟放大。

注：以上为典型量级，生产前务必用 ghz/自有负载实测。