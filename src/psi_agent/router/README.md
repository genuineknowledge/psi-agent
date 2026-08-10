# Router：分流、广播聚合与 Fallback

`psi_agent.router` 把一组 OpenAI Chat Completions 兼容的 AI 服务暴露为一个统一
`POST /chat/completions` 端点，支持三种显式模式：

- `routing`：Selector 从候选中选择一个目标，Router 流式转发该目标的响应。
- `aggregation`：同一个请求并发广播给全部目标，再由专用 Aggregator 综合反馈并流式返回。
- `fallback`：按配置顺序完整尝试目标，只重放首个成功响应；全部失败时返回 Router SSE error。

Router 不保存正式会话历史、不执行工具，也不实现同目标自动重试、熔断、健康检查或 Planner
式任务拆分。会话历史与工具执行继续由 Session 负责。

## 拓扑

```text
Channel -> Session -> Router.session_socket
                        |-- routing: router_socket(Selector) -> one upstream
                        |-- aggregation: all upstreams -> router_socket(Aggregator)
                        `-- fallback: upstreams in order; first complete success wins
                  <- single-choice OpenAI-compatible SSE
```

`router_socket` 在 routing 模式下是 Selector，在 aggregation 模式下是专用
Aggregator；fallback 必须传 `None`。aggregation 中控制 AI 不得同时出现在 upstream 列表里。

每个 upstream 用 `backend_type="ai"` 或 `backend_type="router"` 显式标记，因此三个模式可以
通过各自公开的 `session_socket` 任意无环组合，不存在固定的 Routing → Aggregation → Fallback
流水线。普通二元 upstream 继续默认指向 AI；三元 upstream 的第三项用于指向 Router。

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
    require_all_targets=True,
    control_request_overrides={"max_tokens": 256},
    target_request_overrides={"max_tokens": 4096},
    candidate_request_overrides={"candidate-2": {"max_tokens": 8192}},
    candidate_timeouts={"candidate-2": 180},
)
await router.run()
```

可独立使用 `RoutingRouter`、`AggregationRouter` 或 `FallbackRouter`。统一入口按 upstream 配置顺序生成
`candidate-1`、`candidate-2` 等内部编号；Socket 永不暴露给模型。

```python
fallback = Router(
    session_socket="./fallback.sock",
    router_socket=None,
    mode="fallback",
    upstream=[
        ("./primary.sock", "primary model"),
        ("./nested.sock", "nested router", "router"),
    ],
    target_timeout=60,
)
```

### CLI

```text
psi-agent router \
  --mode fallback \
  --session-socket ./fallback.sock \
  --router-socket None \
  --upstream ./primary.sock "primary" ./nested.sock "nested router" \
  --upstream-types ai router \
  --target-timeout 60 \
  --max-context-chars 12000
```

CLI 的 `--upstream` 仍是同序的 Socket/description 二元序列；组合时用等长的
`--upstream-types` 标记每项为 `ai` 或 `router`。省略该选项时全部默认为 AI。Fallback 的
`--router-socket` 必须传 `None`；Routing/Aggregation 则必须传控制 AI Socket。

### YAML

```yaml
- type: router
  mode: fallback
  session_socket: ./fallback.sock
  router_socket: null
  upstream:
    - [./primary-ai.sock, primary model]
    - [./backup-ai.sock, backup model]
  router_timeout: null
  target_timeout: 60

- type: router
  mode: routing
  session_socket: ./routing.sock
  router_socket: ./selector-ai.sock
  upstream:
    - [./fallback.sock, resilient general model, router]
    - [./specialist-ai.sock, specialist model, ai]

- type: router
  mode: aggregation
  session_socket: ./aggregation.sock
  router_socket: ./aggregator-ai.sock
  upstream:
    - [./routing.sock, routed answer, router]
    - [./review-ai.sock, independent review, ai]
  max_context_chars: 12000
  require_all_targets: true
  control_request_overrides:
    max_tokens: 8192
  target_request_overrides:
    max_tokens: 4096
  candidate_request_overrides:
    candidate-2:
      max_tokens: 8192
  candidate_timeouts:
    candidate-2: 180
```

以上配置表达 `Aggregation → Routing → Fallback`；改变引用方向即可构造其他无环顺序。
每层独立应用自己的 `target_timeout`。外层 timeout 若短于内层最坏执行时间，会取消整个
内层调用；系统不会按嵌套深度自动放大 timeout。

`control_request_overrides` 只作用于当前层的 Selector 或 Aggregator；fallback 没有控制模型，
因此该字段必须为空。`target_request_overrides` 作用于当前层全部 targets，
`candidate_request_overrides` 再按稳定的 `candidate-N` 做浅层覆盖。优先级为
“调用方公开请求 < 全体 target 覆盖 < 单 candidate 覆盖”，嵌套 Router 到达下一层后再应用
下一层自己的配置。`candidate_timeouts` 同样按 candidate 覆盖共享 `target_timeout`。

请求覆盖是顶层浅合并，不递归合并嵌套 object；所有值都会深拷贝，调用方对象不会被修改。
`messages`、`model`、`routing`、`stream` 是协议保护字段，不能通过上述覆盖项改写。
这使 Selector、evidence leaf、Aggregator 和最终回答模型可以使用不同的 `max_tokens`、
供应商扩展参数与 timeout，而不会把内部路由元数据暴露给普通 AI。

## 请求边界

Router 深拷贝公开请求，删除客户端 `model`，强制 `stream=true`，其余已知和未知参数均
透传。AI 与 Selector/Aggregator 控制边删除内部 `routing`；显式 Router 边保留
`routing.session_id` 和同一回合的 UUID `routing.trace_id`，并把当前 candidate ID 追加到
`routing.path`。请求必须包含 object 列表形式的 `messages`；`tools` 存在时也必须是 object 列表。

所有协议边界坚持显式单 choice：0 choice 作为心跳跳过，多 choice 拒绝。HTTP 响应提交前
的请求错误返回 HTTP 400 JSON；提交后的错误返回 `finish_reason="error"` 的 SSE 帧。

## Router 状态协议

Router 在正文之外发送独立的 `delta.router_status` SSE 帧；旧客户端可忽略该未知字段，状态帧
不占用 `content`、`reasoning` 或 `tool_calls`：

```json
{
  "choices": [{
    "index": 0,
    "delta": {"router_status": {
      "version": 1,
      "trace_id": "123e4567-e89b-12d3-a456-426614174000",
      "mode": "fallback",
      "phase": "attempting",
      "depth": 0,
      "attempt": 2,
      "total": 3
    }},
    "finish_reason": null
  }]
}
```

- routing：普通轮次发送 `selecting → generating`；工具 sticky 轮仅发送 `generating`。
- aggregation：发送 `collecting → synthesizing`；部分分支失败但允许综合时，后者带
  `degraded=true`，并用 `completed/total` 表示收集边界。
- fallback：每个候选发送 `attempting`，失败且仍有后继时发送 `switching`，首个成功候选在
  正文回放前发送 `replaying`；`attempt` 从 1 开始。
- 嵌套 Router 共享 `trace_id`，`depth` 等于当前 `routing.path` 长度。fallback 不回放缓冲中的
  内层状态，避免陈旧或失败候选进度泄漏。
- 状态只包含生命周期与计数，不包含 candidate ID、Socket、模型名、prompt、正文或错误详情。

## aggregation 数据流

1. 为每个 target 创建独立请求副本，并在一个 AnyIO task group 中同时调用。
2. 普通分支失败只写入对应结果槽，不取消其他分支；调用者取消会取消整个 task group。
3. 反馈按配置顺序排列，而不是按异步完成顺序排列。
4. 默认至少一个分支成功时，将成功材料和脱敏失败摘要交给 Aggregator；全部失败直接报错。
   `require_all_targets=True` 时，每个分支都必须成功并以 `stop` / `tool_calls` 完整结束，否则报错且
   不调用 Aggregator。该严格模式适合需要保证处理忠实度的受控实验。
5. Aggregator 的流式响应是唯一对 Session 可见的最终响应。

反馈不包含分支 `reasoning` 或真实 Socket。错误摘要中的所有私有 Socket 表示都会替换为
`<private-socket>`，并限制为 512 字符。

`max_context_chars` 只约束成功反馈的 `content` 与 tool function `arguments`。超预算时按
字段数量确定性均分字符配额，保留头尾并插入截断标记；候选编号、描述、状态、tool ID 和
function name 始终保留。

## fallback 数据流

1. 从第一个候选开始，一次只调用一个 target，并完整缓冲它的 SSE stream。
2. 连接、HTTP、协议、error finish、无真实完成、空内容和 reasoning-only 都视为失败。
3. 只有非空白 content 或结构完整的 tool calls 才算成功。
4. 首个成功候选的原始已验证 events 按顺序重放；失败候选的任何 event 都不会泄漏。
5. 所有候选失败时返回一条有序、限长且 Socket 脱敏的 Router SSE error。

## 工具调用

- upstream 可看到完整 tools schema，但它们的 `tool_calls` 只作为聚合材料。
- 只有 Aggregator 返回的 `tool_calls` 会到达 Session。
- Session 执行工具后携完整更新历史重新请求 Router。
- 下一次 aggregation 请求重新广播所有 upstream；不续接分支私有状态。
- routing 模式继续用 `routing.session_id` 在一次工具链中保持目标 sticky。
- routing 与 fallback 的 sticky 实际按 `(routing.session_id, routing.path)` 隔离。
- fallback 的工具轮先重试上次成功候选；若失败，仅向它之后的候选继续，不回到列表开头。

## 失败矩阵

| 状况 | 行为 |
|---|---|
| 一个或多个 upstream 成功 | 带成功材料和失败摘要调用 Aggregator |
| strict aggregation 任一 upstream 失败或未完整结束 | Router SSE error；不调用 Aggregator |
| 全部 upstream 失败 | Router SSE error；不调用 Aggregator |
| Aggregator 失败、空响应或 error finish | Router SSE error；不 fallback |
| Fallback 当前 target 失败 | 丢弃其全部缓冲 events，串行尝试下一 target |
| Fallback 全部 target 失败 | 一条脱敏 Router SSE error |
| 客户端取消 | 关闭 Aggregator stream，并取消仍运行的分支 |

## 验证

```text
uv run pytest -q tests/psi_agent/router
uv run pytest -q tests/integration/test_serial_multi_ai_router.py tests/integration/test_fallback_router_composition.py
uv run ruff check src/psi_agent/router tests/psi_agent/router
uv run ty check src/psi_agent/router tests/psi_agent/router
```

以上命令验证协议与组合不变量。真实后端之间的质量、可靠性、成本和时延对照实验见
[`tests/evals/router/README.md`](../../../tests/evals/router/README.md)；付费且存在随机性的效果评测刻意不进入常规
pytest。
