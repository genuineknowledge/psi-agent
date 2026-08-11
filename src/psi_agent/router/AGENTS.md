# Router 层开发约定

Router 是无状态的 Chat Completions/SSE 组合层。修改本目录时同时遵守仓库根
`AGENTS.md`，尤其是 AnyIO、单 choice、`aclosing()`、Socket 平台门控、零 suppressions
和 `setup_logging` 第一行约束。

## 请求 trace_id

Router 同时接受内部请求头 `X-Psi-Trace-Id` 与 `routing.trace_id`；缺失时在入口创建，二者同时存在时必须一致。
同一 UUID 进入 Router 状态、边界日志、Selector、候选、Aggregator 和嵌套 Router 调用。跨内部 HTTP 边界使用请求头；
仅嵌套 Router 的请求体保留 `routing.trace_id/path`。控制模型和普通 AI 的 body 必须继续剥离 `routing`，防止内部元数据
进入外部 Provider。上游响应头或嵌套状态 trace 不一致时按协议错误处理。

## 模块边界

```text
router/
├── entry.py          # 统一 Router facade，只按显式 mode 组装策略
├── client.py         # Socket-aware HTTP/SSE 客户端
├── request.py        # 类型化 target 请求复制与私有 routing scope
├── privacy.py        # 私有 Socket 错误摘要脱敏
├── models.py         # 共享 mode、target、scope 与 completion 类型
├── server.py         # 公共 HTTP/SSE 边界，不写 mode 分支
├── routing/          # Selector 选择一个目标 + 工具链 sticky
├── aggregation/      # 全目标并发广播 + 专用 Aggregator 综合
└── fallback/         # 按序完整尝试 + 首个成功响应重放
```

共享传输、请求复制、错误脱敏和模型放根包；模式特有逻辑必须留在同名子包。不要在策略中
判断相邻 Router 的具体 mode，也不要让 server 理解 Selector、Aggregator 或 Fallback。

## Socket 所有权

- `session_socket`：Router 对 Session 监听的地址。
- `router_socket`：统一入口的专用控制 AI；routing 为 Selector，aggregation 为 Aggregator，
  fallback 必须为 `None`。
- target Socket：只存在于本地配置，绝不进入 prompt、日志反馈或外部请求元数据。
- aggregation 的 Aggregator 必须专用，不得复用为 target；routing 允许 Selector 同时是候选。
- target 必须用 `backend_type="ai"` 或 `backend_type="router"` 显式标记组合边；不得按 Socket
  字符串猜测下游类型。

所有地址经 `psi_agent._sockets` 解析。Windows 裸路径和非 Windows Named Pipe 的 fail-fast
检查是刻意设计，不得绕过。

## 请求复制

控制 AI 始终调用 `copy_public_request_body()`；策略 target 调用 `copy_target_request_body()`：

1. 深拷贝输入，禁止修改 caller dict。
2. 所有边删除 `model`；AI/control AI 边删除 `routing`。
3. Router 边规范化 `routing`，保留同一回合的 UUID `routing.trace_id`，并把当前 candidate ID
   追加到 `routing.path`；AI/control AI 边不得接收 trace。
4. `routing.path` 只允许稳定 candidate ID，且必须与非空 `routing.session_id` 同时出现。
5. 强制 `stream=True`，其余字段（含未知扩展字段）全部透传。

本地请求预算通过三层显式覆盖实现：控制模型使用 mode config 的
`selector_request_overrides` / `aggregator_request_overrides`；所有 targets 使用
`target_request_overrides`；单个 target 再使用 `candidate_request_overrides`。覆盖是顶层浅合并，
但输入和值都必须深拷贝。`messages`、`model`、`routing`、`stream` 永远是保护字段，任何配置层都
不得改写。fallback 不存在控制模型，非空 `control_request_overrides` 必须 fail-fast。
`RouterTarget.timeout` 非空时优先于 mode config 的共享 `target_timeout`。

Selector prompt 是例外：它只接收候选编号/描述、压缩后的对话与工具摘要，不接收私有
Socket 或原始完整请求。

## SSE 约束

- 每个有效 event 恰好一个 choice；0 choice 静默跳过，多 choice 抛错。
- `finish_reason="compaction_needed"` 是辅助帧，不覆盖真实 completion finish。
- `finish_reason="error"` 转换为 Router 错误。
- Router 进度只能使用独立的 `delta.router_status` 帧，不得复用 `reasoning` 或 `content`。
  共享 schema 位于 `psi_agent._router_status.RouterStatus`，当前 `version=1`；所有状态均携带
  `trace_id`、`mode`、`phase` 与非负 `depth`。状态帧的 `finish_reason` 恒为 `None`。
- phase 按 mode 封闭：routing 为 `selecting/generating`，aggregation 为
  `collecting/synthesizing`，fallback 为 `attempting/switching/replaying`。aggregation 可用
  `completed/total/degraded`，fallback 可用 `attempt/total`。
- `router_status` 是面向 UI 的安全元数据：不得包含 candidate ID、描述、Socket、prompt、响应正文、
  错误详情或模型名。嵌套 Router 沿用同一 trace，并以 `routing.path` 长度作为 `depth`。
- fallback 只回放外层状态；成功候选缓冲中的内层 `router_status` 必须丢弃，避免延迟回放陈旧进度。
- 每个进入/离开 Router 的 chunk 都写 DEBUG 日志。
- 每个 async generator 必须经 `aclosing()` 消费；提前退出和取消必须关闭上游连接。
- aiohttp session/response/runner 的跨 await 清理放在 shielded CancelScope 中。

## routing 不变量

- 每个普通用户轮次重新调用 Selector。
- Selector 只能返回严格 `{"candidate_id":"..."}`，再由本地映射解析 Socket。
- sticky key 是 `(routing.session_id, routing.path)`，同一 Session 的不同组合路径必须隔离。
- 仅 `finish_reason="tool_calls"` 保留 sticky；正常结束、错误、断连或新用户轮次均清理。

## aggregation 不变量

- 每回合把同一公开请求并发发送给全部 targets，不做 Planner、子任务拆分或候选子集选择。
- 用预分配 slot 保证反馈始终按配置顺序，不按完成顺序。
- 普通分支异常隔离；取消异常必须继续传播并取消整个 task group。
- 至少一个分支成功才调用 Aggregator；全部失败直接 `AggregationError`。
- `require_all_targets=False` 保持上述部署态降级语义；严格实验可设为 `True`，此时每个分支都必须
  成功并以 `stop` / `tool_calls` 完整结束，否则不进入 Aggregator。
- 分支 reasoning 永不进入反馈；分支 tool calls 只作为材料。
- 只有 Aggregator 的 content/reasoning/tool_calls 可以返回 Session。
- `discard()` / `clear()` 是显式无状态 no-op；工具结果轮重新广播全部 targets。
- 失败摘要须替换原始、repr 和转义形式的私有 Socket，并截到 512 字符。
- 动态材料压缩必须确定、可复现，不得依赖异步完成顺序。

## fallback 不变量

- targets 严格按配置顺序串行尝试；同一时刻只能运行一个 attempt，不并发、不回绕。
- 每个 attempt 必须通过共享 `buffered_complete()` 完整消费和验证；失败 attempt 的任何 event
  都不得发送给调用方。
- 非空白 content 或结构完整的 tool calls 才是可用结果；reasoning-only 和空完成均失败。
- 首个可用结果按原始顺序重放全部已验证 events，保留未知顶层扩展和 compaction 辅助帧。
- 工具轮从相同 sticky 索引开始；该候选失败后只尝试其后的 targets。
- 取消立即传播并清理 scope；重放阶段断连不得触发新 attempt。
- 全部失败抛 `FallbackError`；摘要按配置顺序记录 candidate ID、异常类型和脱敏后的有限文本。

## 有意不支持

- Planner、动态任务拆分、请求选择候选 Socket。
- 默认模型、同 target 自动重试、熔断、健康检查、负载均衡。
- 自动拓扑发现、运行时循环检测和跨进程 sticky 持久化。
- Router 内会话持久化或 workspace tool 执行。
- 已删除的 `RouterClient`、`UpstreamResult`、`stream_raw`、`Orchestrator` 等旧 API。

## 测试位置

- 共享边界：`tests/psi_agent/router/test_*.py`
- aggregation：`tests/psi_agent/router/aggregation/`
- fallback：`tests/psi_agent/router/fallback/`
- 真实 Session 链路：`tests/integration/test_serial_multi_ai_router.py`
- 3×3 组合矩阵、六种三层排列与分支图：`tests/integration/test_fallback_router_composition.py`

并发测试用 `anyio.Event` / cancel scope，不用固定 sleep。测试提前退出任务组前先 cancel，避免
常驻 aiohttp server 把 `__aexit__(None, None, None)` 永久挂住。
