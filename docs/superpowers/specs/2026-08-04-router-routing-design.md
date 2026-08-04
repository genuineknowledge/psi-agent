# Unified Router Single-Target Routing Design Spec

## Status and Scope

状态：已同步至 2026-08-04 的统一 Router 实现。

本文档描述 `src/psi_agent/router/` 当前的单目标分流（Routing）设计，以及它在统一
`Router`、CLI、YAML 批量启动、Gateway 和 SPA 中的公开配置形状。

Routing 不再是 Router 包唯一的顶层模式。统一入口显式接受：

```python
Router(
    session_socket: str,
    router_socket: str,
    mode: RouterMode | str,
    upstream: list[tuple[str, str]],
    router_timeout: float | None = 30.0,
    target_timeout: float | None = None,
    max_context_chars: int = 12_000,
    verbose: bool = False,
)
```

当 `mode="routing"` 时，`router_socket` 是 Selector AI，所有 `upstream` 是候选目标，
Router 选择一个目标并流式转发。`mode="aggregation"` 的广播聚合行为由
`2026-08-04-broadcast-aggregation-router-design.md` 单独定义。

保留 `RoutingRouter` 作为可独立嵌入的 Routing 专用入口；CLI、YAML 和 Gateway 使用统一
`Router`。

## Problem Statement

Session 需要把一个 OpenAI Chat Completions 请求交给多个能力、成本或模型类型不同的 AI
后端之一，但不应知道选择规则，也不应看到候选服务的私有 Socket。Router 必须在保持单
choice SSE 协议的前提下：

1. 用独立 Selector AI 根据任务语义选择一个候选；
2. 将不透明 `candidate_id` 映射为进程内可信配置中的目标 Socket；
3. 把原公开请求流式转发给唯一目标；
4. 在一次 tool-call 链内保持目标稳定；
5. 与统一 Router 的传输、配置和 Gateway 生命周期共存。

## Goals

- 对 Session 暴露统一的 `POST /chat/completions` 流式接口。
- 每个普通用户轮次只选择并调用一个候选 AI。
- Selector 只看到候选 ID、描述、压缩后的对话和工具摘要，不看到私有 Socket。
- 严格验证 Selector 输出，不允许模型构造目标地址。
- 通过共享请求复制函数深拷贝公开参数，只删除 `model` 和 `routing`，并强制
  `stream=true`。
- 与 psi-agent 的单 choice SSE、`compaction_needed` 和 `finish_reason="error"` 扩展兼容。
- 在连续工具调用 POST 之间复用同一目标，工具链结束后释放 sticky。
- 保持 `RoutingRouter`、`RoutingConfig`、`RoutingStrategy` 和 `RoutingTarget` 的嵌入接口。
- 统一 CLI、YAML、Gateway REST/state 和 SPA 的字段语义。

## Non-Goals

- Routing 不广播请求，也不合并多个回答。
- Routing 不在目标失败时自动选择第二个候选，不实现 fallback、重试、熔断或健康检查。
- Router 不保存正式 conversation history，不加载 workspace tools，也不执行工具。
- Router 不保证同一 Session 的所有普通轮次永久使用同一目标。
- `_sticky_targets` 不持久化，Router 重启后允许重新选择。
- Channel 或单次用户请求不能动态指定候选 Socket。
- 不恢复 Planner、子任务拆分、旧 buffered server 或其他已删除 API。

## Architecture

```text
Channel
  -> Session
  -> Router.session_socket
       -> Router(mode="routing")
            -> RoutingConfig
            -> RouteSelector
                 -> router_socket / Selector AI
                 <- {"candidate_id":"candidate-N"}
            -> local candidate_id -> RouterTarget
            -> selected upstream Socket
            <- validated single-choice SSE
  -> Session
  -> Channel
```

工具继续轮次存在有效 sticky 时跳过 Selector：

```text
Session POST, last message role=tool, same routing.session_id
  -> RoutingStrategy._sticky_targets
  -> previous selected upstream
```

## Module Boundaries

| File | Responsibility |
|---|---|
| `router/models.py` | 跨模式的 `RouterMode`、`RouterTarget`、`CompletionResult`。 |
| `router/request.py` | 深拷贝公开请求，删除 `model`/`routing`，强制流式。 |
| `router/client.py` | Socket-aware HTTP/SSE 客户端及单 choice 校验。 |
| `router/server.py` | 共享 OpenAI-compatible HTTP/SSE 边界和服务生命周期。 |
| `router/entry.py` | 统一 `Router` facade，按 `mode` 组合策略。 |
| `router/routing/models.py` | `RoutingConfig`、`SelectionResult` 与兼容别名。 |
| `router/routing/prompts.py` | 严格 Selector prompt。 |
| `router/routing/selector.py` | Selector 请求投影、上下文压缩和可信 ID 映射。 |
| `router/routing/strategy.py` | 单目标转发和 tool-run sticky 状态。 |
| `router/routing/entry.py` | 可独立嵌入的 `RoutingRouter`。 |

共享 server/client/request 不包含 Routing 算法。候选选择和 sticky 只存在于 `routing/`。

## Shared Domain Model

### `RouterMode`

```python
class RouterMode(StrEnum):
    ROUTING = "routing"
    AGGREGATION = "aggregation"
```

`mode` 是必填字段，不再提供缺省模式或隐式旧 Routing 行为。

### `RouterTarget` and `RoutingTarget`

```python
RouterTarget(
    candidate_id="candidate-1",
    socket="http://127.0.0.1:18103",
    description="Programming, debugging, testing, and architecture.",
)
```

`RoutingTarget` 是 `RouterTarget` 的兼容别名，而不是第二套类型。约束为：

- 三个字段去除首尾空白；
- `candidate_id` 匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`；
- `socket` 和 `description` 非空；
- Selector 只看到 `candidate_id` 与 `description`；
- 统一入口按配置顺序生成 `candidate-1`、`candidate-2` 等稳定 ID。

## Configuration

### Unified `Router`

Routing 模式的字段映射为：

| Unified field | Routing field | Meaning |
|---|---|---|
| `session_socket` | `RoutingConfig.session_socket` | Router 对 Session 提供服务的地址。 |
| `router_socket` | `selector_socket` | Selector AI 地址。 |
| `upstream` | `targets` | 候选 `(socket, description)` 列表。 |
| `router_timeout` | `selector_timeout` | Selector 请求总超时。 |
| `target_timeout` | `target_timeout` | 被选目标请求总超时。 |
| `max_context_chars` | `max_selection_chars` | Selector 对话字符预算。 |

统一入口校验 `upstream` 必须是非空 `list[tuple[str, str]]`，再转换为共享
`RouterTarget`。具体 Socket 冲突、timeout 和预算校验由 `RoutingConfig` 负责。

### `RoutingConfig`

```python
RoutingConfig(
    session_socket="http://127.0.0.1:18100",
    selector_socket="http://127.0.0.1:18101",
    targets=[...],
    selector_timeout=30.0,
    target_timeout=None,
    max_selection_chars=12_000,
)
```

构造时必须满足：

- `session_socket`、`selector_socket` 去除空白后非空且不相等；
- `targets` 非空，且每项是共享 `RouterTarget`；
- `candidate_id` 唯一，target Socket 唯一；
- target Socket 不得等于公开 `session_socket`；
- timeout 是有限正数或 `None`，不接受 `bool`；
- `max_selection_chars` 是正整数，不接受 `bool`；
- `targets` 最终规范化为 tuple。

Routing 刻意允许 Selector AI 同时出现在候选目标中；只有 aggregation 要求 Aggregator 专用且
不能作为 upstream。

## Public Request Contract

Router 只提供：

```text
POST /chat/completions
Content-Type: application/json
```

典型请求：

```json
{
  "messages": [{"role": "user", "content": "请定位这个 Python 并发问题"}],
  "tools": [],
  "stream": true,
  "temperature": 0.2,
  "routing": {"session_id": "session-001"}
}
```

共享 server 校验：

- body 是 JSON object；
- `messages` 是 object 列表；
- `tools` 省略时视为空列表，存在时是 object 列表；
- `stream` 省略时视为 `true`，显式值必须严格等于 `true`；
- `routing` 可省略，存在时是 object；
- `routing.session_id` 可省略，存在时是去除空白后非空字符串。

RoutingStrategy 独立调用时会重复关键结构校验，不依赖 HTTP 边界替它信任输入。

### Shared Public Request Copy

`copy_public_request_body()` 被 Routing 和 Aggregation 共同使用。它：

1. 深拷贝输入 body；
2. 删除 `model`，因为模型由目标 AI 服务进程配置；
3. 删除内部 `routing` 元数据；
4. 强制 `stream=True`；
5. 保留 `messages`、`tools`、`tool_choice`、采样参数和未知公开参数；
6. 不修改调用方原 dict。

## Selector Protocol

### Request Projection

Selector 收到确定性的分类请求：

```json
{
  "messages": [
    {"role": "system", "content": "strict routing instructions"},
    {
      "role": "user",
      "content": "{\"candidates\":[...],\"conversation\":[...],\"available_tools\":[...]}"
    }
  ],
  "stream": true,
  "temperature": 0
}
```

- candidates 按配置顺序，仅含 ID 和 description；
- conversation 只保留 role/content 的分类摘要；
- 多模态 content 只表示为 block 数量，不传原始数据；
- tools 只传有效 function name 和最多 256 字符描述；
- 不传完整工具 Schema、调用方 model、routing 元数据或任何 Socket。

### Conversation Compaction

Selector 使用字符预算而非 token 估算：

1. 规范化可用消息；
2. 从最新消息反向遍历；
3. 完整消息能放入时保留；
4. 第一条超预算候选在剩余预算大于 64 时保留 content 尾部；
5. 首次无法放入后停止；
6. 恢复原时间顺序。

该预算只限制分类上下文，不改变发给目标的公开请求。

### Strict Decision Validation

Selector 必须以 `finish_reason="stop"` 结束，不得产生 tool calls，content 必须解析为唯一形状：

```json
{"candidate_id":"candidate-1"}
```

object 只能有 `candidate_id` 一个键，值必须是本地配置中存在的字符串 ID。Markdown、解释、
额外字段、未知 ID、非 `stop` 和 tool calls 均产生 `RouteSelectionError`。Socket 只通过本地
`candidate_id -> RouterTarget` 映射取得。

## Routing Algorithm

### Ordinary Turn

最后一条消息 role 不是 `tool` 时：

```text
validate body
  -> discard stale sticky for session_id
  -> call Selector
  -> validate candidate_id
  -> remember selection when session_id exists
  -> copy public request
  -> stream exactly one selected target
  -> keep sticky only for finish_reason="tool_calls"
```

因此同一 Session 的下一普通用户问题会重新选择。

### Tool-Call Sticky

Session 执行工具后，会用同一 `routing.session_id` 和末条 `role="tool"` 的完整消息再次 POST。
`RoutingStrategy` 以 `dict[str, SelectionResult]` 保存进程内 sticky：

- tool continuation 且存在 sticky：跳过 Selector，复用目标；
- tool continuation 但 sticky 不存在：重新调用 Selector；
- 目标最终 `tool_calls`：保留 sticky；
- `stop`、`length`、`content_filter`、`error` 或其他终态：删除 sticky；
- 上游异常、未完整消费、客户端断开或写流失败：删除 sticky；
- 新普通轮次、显式 `discard()` 或服务 `clear()`：删除对应状态。

没有 `routing.session_id` 时不建立跨 POST sticky。

## Shared HTTP/SSE Client

`RouterHttpClient` 通过 `resolve_connector_and_endpoint()` 支持 TCP、Unix Socket 和 Windows
Named Pipe。每次调用创建独立 `aiohttp.ClientSession`，并在 shielded cleanup 中关闭。

`stream()`：

- 解析多行 `data:` SSE；
- 跳过 0 choice 心跳；
- 拒绝多 choice、非 object choice/delta 和非法 finish reason；
- 把 `delta=null` 规范化为 `{}`；
- 识别 `[DONE]`；
- 要求至少一个非 `compaction_needed` 终态。

`complete()` 用于 Selector 的缓冲读取，累积 content/reasoning/tool call 分片；Routing 随后严格
拒绝 Selector tool calls，并验证其单一 JSON 决策。

## Response, Errors, and Cancellation

正常情况下，目标模型的单 choice SSE 原样经过共享 server，末尾追加 `[DONE]`。

- prepare 前的 JSON/结构错误：HTTP 400 OpenAI 风格错误 JSON；
- prepare 后的 Selector、目标连接或策略错误：单 choice
  `finish_reason="error"` SSE；
- 客户端断开：清理 sticky，不尝试继续写错误帧；
- 0 choice 心跳：静默跳过；
- 多 choice：协议错误。

`Router.run()` 和 `RoutingRouter.run()` 都以
`setup_logging(verbose=self.verbose)` 作为第一行可执行语句。server、strategy 和 client 消费
async generator 时使用 `aclosing()`；启动失败、取消和 shutdown 都通过 shielded cleanup 关闭
runner/连接并清空 sticky。

## CLI and YAML

CLI 使用统一入口：

```text
psi-agent router \
  --mode routing \
  --session-socket ./router.sock \
  --router-socket ./selector-ai.sock \
  --upstream ./code.sock "coding" ./research.sock "research" \
  --router-timeout 30 \
  --max-context-chars 12000
```

YAML：

```yaml
- type: router
  mode: routing
  session_socket: ./router.sock
  router_socket: ./selector-ai.sock
  upstream:
    - [./code.sock, coding]
    - [./research.sock, research]
  router_timeout: 30
  target_timeout: null
  max_context_chars: 12000
```

`_run.py` 把 YAML 的二元 list 规范化为 tuple。旧 `default_socket` 不再读取。

## Gateway and SPA Contract

Gateway 对用户保存 AI ID，在启动 Router 前本地映射成 Socket。Router 记录字段为：

- `id`、`name`、`socket`、`mode`；
- `router_ai_id`：Routing 模式下代表 Selector AI；
- `upstreams`：`ai_id` 与能力 description；
- `router_timeout`、`target_timeout`、`max_context_chars`。

Routing 允许 `router_ai_id` 同时出现在 upstream；Aggregation 禁止。REST/OpenAPI 和 SPA 使用同一
字段集，不再包含 `default_ai_id` 或 `max_context_length`。SPA 根据 mode 把 Router AI 标为
Selector 或 Aggregator。

恢复旧 Gateway state 时忽略 `default_ai_id`，并在缺少新字段时把 `max_context_length` 单向映射为
`max_context_chars`；下一次正常保存只写新结构。Router-backed Session 需要普通 AI 做标题或摘要
时使用 `router_ai_id`。

## Compatibility

继续保留：

- `RoutingRouter`、`RoutingStrategy`、`RoutingConfig`；
- `RoutingTarget`，作为共享 `RouterTarget` 的别名；
- `RouteSelector`、`SelectionResult`、`build_selector_messages`；
- `RouterHttpClient`、`CompletionResult`；
- `create_router_app()`、`serve_router()` 和 `RouterStrategy` protocol。

不恢复：

- Planner、`PlannedTask`、旧 `RoutingRun` 状态机；
- `RouterClient`、`UpstreamResult`、`stream_raw`、`Orchestrator`；
- buffered `process()` server；
- `default_socket`、`default_ai_id`、缺省 mode。

## Testing Strategy

Routing 回归由以下范围覆盖：

- `tests/psi_agent/router/test_models.py`：共享 target 与兼容别名；
- `tests/psi_agent/router/test_request.py`：公开请求复制；
- `tests/psi_agent/router/test_client.py`：单 choice SSE 与 buffered completion；
- `tests/psi_agent/router/test_routing.py`：Routing config、Selector 和 sticky 策略；
- `tests/psi_agent/router/test_entry.py`：统一 Router 的模式组合与字段映射；
- `tests/psi_agent/router/test_server.py`：共享 HTTP/SSE 边界；
- `tests/psi_agent/test_cli.py`、`tests/psi_agent/test_run.py`：CLI/YAML 接线；
- Gateway manager/state/OpenAPI/server 测试：AI ID 映射和迁移契约。

## Acceptance Criteria

1. `mode="routing"` 经过一次 Selector 决策后只访问一个已配置目标。
2. Selector 看不到目标 Socket，且只能返回一个已配置 `candidate_id`。
3. 目标请求只移除 `model`/`routing`，其余字段深拷贝透传并强制流式。
4. 同一 session 的 tool continuation 复用目标，工具链结束、异常或断连后释放 sticky。
5. 统一 Router 正确映射 timeout 和字符预算，同时保留独立 `RoutingRouter`。
6. Routing 允许 Selector AI 同时作为候选；Aggregation 的专用服务约束不影响 Routing。
7. CLI、YAML、Gateway、OpenAPI 和 SPA 使用当前统一字段，不读取旧 default 字段。
8. 有效上游事件符合单 choice SSE，prepare 前后错误使用约定的两类边界。
9. 取消、启动失败和关闭不会遗留 client session、runner 或 sticky 状态。
10. Routing 不承担广播聚合、history、工具执行、fallback 或 Planner 职责。
