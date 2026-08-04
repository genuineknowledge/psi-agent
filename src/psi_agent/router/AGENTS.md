# Router 层开发约定

Router 是无状态的 Chat Completions/SSE 组合层。修改本目录时同时遵守仓库根
`AGENTS.md`，尤其是 AnyIO、单 choice、`aclosing()`、Socket 平台门控、零 suppressions
和 `setup_logging` 第一行约束。

## 模块边界

```text
router/
├── entry.py          # 统一 Router facade，只按显式 mode 组装策略
├── client.py         # Socket-aware HTTP/SSE 客户端
├── request.py        # 公开请求深拷贝与私有字段剥离
├── models.py         # RouterMode、RouterTarget、CompletionResult
├── server.py         # 公共 HTTP/SSE 边界，不写 mode 分支
├── routing/          # Selector 选择一个目标 + 工具链 sticky
└── aggregation/      # 全目标并发广播 + 专用 Aggregator 综合
```

共享传输、错误和模型放根包；模式特有逻辑必须留在同名子包。不要把 aggregation 分支塞进
`RoutingStrategy`，也不要让 server 理解 Selector 或 Aggregator。

## Socket 所有权

- `session_socket`：Router 对 Session 监听的地址。
- `router_socket`：统一入口的专用 Router AI；routing 为 Selector，aggregation 为 Aggregator。
- target Socket：只存在于本地配置，绝不进入 prompt、日志反馈或外部请求元数据。
- aggregation 的 Aggregator 必须专用，不得复用为 target；routing 允许 Selector 同时是候选。

所有地址经 `psi_agent._sockets` 解析。Windows 裸路径和非 Windows Named Pipe 的 fail-fast
检查是刻意设计，不得绕过。

## 请求复制

两个策略统一调用 `copy_public_request_body()`：

1. 深拷贝输入，禁止修改 caller dict。
2. 只删除 `model` 与 `routing`。
3. 强制 `stream=True`。
4. 其余字段（含未知扩展字段）全部透传。

Selector prompt 是例外：它只接收候选编号/描述、压缩后的对话与工具摘要，不接收私有
Socket 或原始完整请求。

## SSE 约束

- 每个有效 event 恰好一个 choice；0 choice 静默跳过，多 choice 抛错。
- `finish_reason="compaction_needed"` 是辅助帧，不覆盖真实 completion finish。
- `finish_reason="error"` 转换为 Router 错误。
- 每个进入/离开 Router 的 chunk 都写 DEBUG 日志。
- 每个 async generator 必须经 `aclosing()` 消费；提前退出和取消必须关闭上游连接。
- aiohttp session/response/runner 的跨 await 清理放在 shielded CancelScope 中。

## routing 不变量

- 每个普通用户轮次重新调用 Selector。
- Selector 只能返回严格 `{"candidate_id":"..."}`，再由本地映射解析 Socket。
- `routing.session_id` 只为一次 Session 工具链保存 sticky target。
- 仅 `finish_reason="tool_calls"` 保留 sticky；正常结束、错误、断连或新用户轮次均清理。

## aggregation 不变量

- 每回合把同一公开请求并发发送给全部 targets，不做 Planner、子任务拆分或候选子集选择。
- 用预分配 slot 保证反馈始终按配置顺序，不按完成顺序。
- 普通分支异常隔离；取消异常必须继续传播并取消整个 task group。
- 至少一个分支成功才调用 Aggregator；全部失败直接 `AggregationError`。
- 分支 reasoning 永不进入反馈；分支 tool calls 只作为材料。
- 只有 Aggregator 的 content/reasoning/tool_calls 可以返回 Session。
- `discard()` / `clear()` 是显式无状态 no-op；工具结果轮重新广播全部 targets。
- 失败摘要须替换原始、repr 和转义形式的私有 Socket，并截到 512 字符。
- 动态材料压缩必须确定、可复现，不得依赖异步完成顺序。

## 有意不支持

- Planner、动态任务拆分、请求选择候选 Socket。
- fallback、默认模型、自动重试、熔断、健康检查、负载均衡。
- Router 内会话持久化或 workspace tool 执行。
- 已删除的 `RouterClient`、`UpstreamResult`、`stream_raw`、`Orchestrator` 等旧 API。

## 测试位置

- 共享边界：`tests/psi_agent/router/test_*.py`
- aggregation：`tests/psi_agent/router/aggregation/`
- 真实 Session 链路：`tests/integration/test_serial_multi_ai_router.py`

并发测试用 `anyio.Event` / cancel scope，不用固定 sleep。测试提前退出任务组前先 cancel，避免
常驻 aiohttp server 把 `__aexit__(None, None, None)` 永久挂住。
