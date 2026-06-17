## 1. 延迟总览矩阵

| 协议 | 连接建立 | 小消息 RTT | 高并发尾延迟 | 丢包表现 | 双向能力 |
|---|---|---|---|---|---|
| HTTP/2 | 1-RTT(0-RTT可达0) | ≈1×网络RTT | ⚠️ TCP HOL,丢包2%时P99升2-3× | ❌ 单流丢包阻塞全部 | ⚠️ 需轮询/Push已弃用 |
| HTTP/3 | 0-RTT重连/1-RTT首连 | ≈1×RTT,TTFB低100-300ms | ✅ 流级独立,P99较H2优15-40% | ✅ 独立流,丢包2%尾延迟改善~30% | ✅ 双向流+DATAGRAM |
| WebSocket | 2×RTT(20-40ms同区) | 0.5-1.2ms(LAN) | ⚠️ 大流量P99升至10ms+ | ❌ 走TCP,丢包触发重传卡顿 | ✅ 全双工 |
| SSE | 1×RTT+TLS(50-150ms) | RTT+1-5ms | ✅ 局域<20ms,瓶颈在扇出 | ⚠️ 默认重连~3s是尖峰主因 | ❌ 单向,回传需另开HTTP |
| gRPC | 1-2RTT(LAN 1-3ms) | unary 0.3-1ms;bidi 0.2-0.5ms | ✅ 同机房稳定,bidi复用连接 | ❌ H2基座,高丢包不及QUIC | ✅ bidi streaming |

## 2. 场景推荐速查

| 实时场景 | 首选 | 次选 | 关键原因 |
|---|---|---|---|
| 聊天/IM | WebSocket | SSE | 双向、生态成熟,50-150ms体感无感 |
| AI流式输出 | SSE | WebSocket | 逐token单向推送,OpenAI/Anthropic均用 |
| 游戏状态同步 | HTTP/3(DATAGRAM) | UDP/WebRTC | 不可靠传输避免重传抖动,TCP系HOL致卡顿 |
| 金融行情推送 | gRPC streaming | WebSocket | 强schema+Protobuf省序列化,首字节快 |
| 微服务间调用 | gRPC | HTTP/2 | 二进制+强类型,比REST快20-60% |
| 直播弹幕 | HTTP/3 | SSE | 万级并发流无HOL,P99更稳 |
| 视频会议信令 | WebSocket | HTTP/3 | 双向长连接契合,可插拔拥塞控制 |
| 实时协作编辑 | WebSocket | HTTP/3 | 双向,传输非瓶颈(OT/CRDT计算主导) |
| IoT数据上报 | gRPC | HTTP/3 | Protobuf省带宽,弱网优于JSON |
| 浏览器通知推送 | SSE | WebSocket | 标准HTTP、自动重连、对CDN/代理友好 |

## 3. 核心发现

1. 公网延迟由 RTT 主导,协议差异主要体现在握手次数与丢包恢复。理想网络下各协议趋同,差距在弱网放大。
2. TCP 系协议(H2/WS/gRPC)的内核层队头阻塞是高丢包尾延迟的根因;QUIC/HTTP3 的流级独立重传是弱网唯一结构性突破。
3. 选型本质是双向性 × 网络质量的二维匹配:单向流式选 SSE,双向选 WS/gRPC,移动弱网选 H3。

## 4. 延迟排名(低 → 高)

1. gRPC bidi — 同机房 0.2-0.5ms,连接复用+二进制帧最优。
2. WebSocket — 2B 帧头、全双工免重握手,LAN 亚毫秒。
3. HTTP/3 — 0-RTT 重连且无 HOL,弱网最稳但 UDP 可能被限速。
4. SSE — 推送≈RTT+几ms,但 ~3s 重连是尖峰。
5. HTTP/2 — 请求-响应优,实时推送靠轮询且 TCP HOL 放大尾延迟。

## 5. 一句话选型建议

聊天选 WebSocket、流式 AI 选 SSE、游戏选 HTTP/3 DATAGRAM、微服务选 gRPC、浏览器推送选 SSE;移动弱网一律优先 HTTP/3,极致低延迟竞技场景则下沉到 UDP/WebRTC。