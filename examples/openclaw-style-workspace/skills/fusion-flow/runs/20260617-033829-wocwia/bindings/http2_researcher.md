# HTTP/2 实时通信延迟分析

## 1. 协议机制
- 多路复用:单 TCP 连接并发流,消除 HTTP/1.1 队头阻塞(应用层),省去多连接握手
- HPACK:头部压缩约 80–90%,小消息收益显著
- 二进制帧:解析快于文本,帧头 9 字节固定开销
- Server Push/流优先级:Push 已被主流浏览器弃用(Chrome 106 移除)

## 2. 延迟数据
- 连接建立:TLS 1.3 1-RTT + ALPN,冷连接约 1×RTT;0-RTT 可达 0
- TTFB:热连接复用,省 1–3 RTT;冷连接受握手主导
- 尾延迟:应用层无 HOL,但 TCP 丢包时全部流阻塞(内核层 HOL),丢包 2% 时 P99 可升 2–3×
- 小消息 <1KB:RTT≈网络 RTT+帧开销,约 1×RTT
- 大消息 >100KB:受 TCP 拥塞窗口/BDP 限制,吞吐接近 H1

## 3. 实时场景
- 聊天推送:Push 不可靠,推荐 WebSocket 或 SSE
- 弹幕:单连接数千流,内存/优先级调度成瓶颈,丢包放大尾延迟
- 游戏同步 <100B 高频:TCP HOL 致抖动,实时性弱于 UDP/QUIC
- 信令:可用,但双向长连接 WebSocket 更契合

## 4. 协议对比
- vs H1.1:页面加载提速约 10–30%(Google 实测),多请求场景明显
- vs H3(QUIC):H3 用 UDP+流级重传,消除内核 HOL,弱网 P99 优 15–40%
- vs WebSocket:需服务端主动全双工推送时选 WS;请求-响应+缓存选 H2

## 5. 最佳实践
- 适用:请求-响应密集、可缓存、弱网少
- 调优:SETTINGS_MAX_CONCURRENT_STREAMS 100–250;帧 16–64KB;HPACK 表 4–64KB;开启 0-RTT

来源:Google SPDY/HTTP2 白皮书、Cloudflare/Fastly 博客、RFC 7540/9113。注:具体数字随网络环境波动,需自测验证。