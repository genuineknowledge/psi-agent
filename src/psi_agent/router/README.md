# Router：分流与广播聚合

`psi_agent.router` 把一组 OpenAI Chat Completions 兼容的 AI 服务暴露为一个统一
`POST /chat/completions` 端点，支持两种显式模式：

- `routing`：Selector 从候选中选择一个目标，Router 流式转发该目标的响应。
- `aggregation`：同一个请求并发广播给全部目标，再由专用 Aggregator 综合反馈并流式返回。

Router 不保存正式会话历史、不执行工具，也不实现 fallback、重试、熔断、健康检查或
Planner 式任务拆分。会话历史与工具执行继续由 Session 负责。

## 拓扑

```text
Channel -> Session -> Router.session_socket
                        |-- routing: router_socket(Selector) -> one upstream
                        `-- aggregation: all upstreams -> router_socket(Aggregator)
                  <- single-choice OpenAI-compatible SSE
```

`router_socket` 在 routing 模式下是 Selector，在 aggregation 模式下是专用
Aggregator。aggregation 中它不得同时出现在 upstream 列表里。

## 公共入口

```python
from psi_agent.router import Router

router = Router(
    session_socket="./router.sock",
    router_socket="./aggregator.sock",
    mode="aggregation",
    upstream=[
        ("./code.sock", "programming and debugging"),
        ("./research.sock", "research and evidence synthesis"),
    ],
    router_timeout=30,
    target_timeout=None,
    max_context_chars=12_000,
)
await router.run()
```

可独立使用 `RoutingRouter` 或 `AggregationRouter`。统一入口按 upstream 配置顺序生成
`candidate-1`、`candidate-2` 等内部编号；Socket 永不暴露给模型。

### CLI

```text
psi-agent router \
  --mode aggregation \
  --session-socket ./router.sock \
  --router-socket ./aggregator.sock \
  --upstream ./code.sock "coding" ./research.sock "research" \
  --router-timeout 30 \
  --target-timeout 60 \
  --max-context-chars 12000
```

### YAML

```yaml
- type: router
  mode: aggregation
  session_socket: ./router.sock
  router_socket: ./aggregator.sock
  upstream:
    - [./code.sock, coding]
    - [./research.sock, research]
  router_timeout: 30
  target_timeout: null
  max_context_chars: 12000
```

## 请求边界

Router 深拷贝公开请求，删除客户端 `model` 和内部 `routing`，强制 `stream=true`，其余
已知和未知参数均透传。请求必须包含 object 列表形式的 `messages`；`tools` 存在时也必须
是 object 列表。

所有协议边界坚持显式单 choice：0 choice 作为心跳跳过，多 choice 拒绝。HTTP 响应提交前
的请求错误返回 HTTP 400 JSON；提交后的错误返回 `finish_reason="error"` 的 SSE 帧。

## aggregation 数据流

1. 为每个 target 创建独立请求副本，并在一个 AnyIO task group 中同时调用。
2. 普通分支失败只写入对应结果槽，不取消其他分支；调用者取消会取消整个 task group。
3. 反馈按配置顺序排列，而不是按异步完成顺序排列。
4. 至少一个分支成功时，将成功材料和脱敏失败摘要交给 Aggregator；全部失败直接报错。
5. Aggregator 的流式响应是唯一对 Session 可见的最终响应。

反馈不包含分支 `reasoning` 或真实 Socket。错误摘要中的所有私有 Socket 表示都会替换为
`<private-socket>`，并限制为 512 字符。

`max_context_chars` 只约束成功反馈的 `content` 与 tool function `arguments`。超预算时按
字段数量确定性均分字符配额，保留头尾并插入截断标记；候选编号、描述、状态、tool ID 和
function name 始终保留。

## 工具调用

- upstream 可看到完整 tools schema，但它们的 `tool_calls` 只作为聚合材料。
- 只有 Aggregator 返回的 `tool_calls` 会到达 Session。
- Session 执行工具后携完整更新历史重新请求 Router。
- 下一次 aggregation 请求重新广播所有 upstream；不续接分支私有状态。
- routing 模式继续用 `routing.session_id` 在一次工具链中保持目标 sticky。

## 失败矩阵

| 状况 | 行为 |
|---|---|
| 一个或多个 upstream 成功 | 带成功材料和失败摘要调用 Aggregator |
| 全部 upstream 失败 | Router SSE error；不调用 Aggregator |
| Aggregator 失败、空响应或 error finish | Router SSE error；不 fallback |
| 客户端取消 | 关闭 Aggregator stream，并取消仍运行的分支 |

## 验证

```text
uv run pytest -q tests/psi_agent/router
uv run pytest -q tests/integration/test_serial_multi_ai_router.py
uv run ruff check src/psi_agent/router tests/psi_agent/router
uv run ty check src/psi_agent/router tests/psi_agent/router
```
