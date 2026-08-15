# Serial Fallback Router 设计规格

日期：2026-08-05  
状态：设计已确认，待实施

## 1. 背景

`psi_agent.router` 当前提供两个同级策略：

- `routing`：由 Selector 从多个 target 中选择一个并流式转发；
- `aggregation`：并发调用全部 target，再由专用 Aggregator 综合输出。

两者都把多个 OpenAI Chat Completions 兼容 Socket 暴露为一个统一 Socket，但尚未提供按优先级尝试上游的 Fallback 策略。当前 Router 文档还把 fallback 列为明确不支持的功能。

本设计新增第三个同级策略 `fallback`。它按配置顺序逐一尝试 target：当前 target 完整失败后才尝试下一个；首个完整成功的响应成为唯一外部输出；全部失败则返回 Router SSE error。

Routing、Aggregation 与 Fallback 必须继续共享相同 HTTP/SSE 边界，并能通过彼此的公开 `session_socket` 任意组成无环调用图，而不是形成固定的 pipeline。

## 2. 目标

1. 在 `src/psi_agent/router/fallback/` 中新增与 `routing/`、`aggregation/` 平行的策略包。
2. 按启动配置顺序串行尝试全部 target，不并发、不随机、不动态重排。
3. 完整缓冲并验证当前 target；只有确认成功后才向调用方发出第一个业务 SSE event。
4. 接受非空白 `content` 或结构完整的 `tool_calls` 作为可用结果。
5. 支持一次 Session 工具链内的候选粘性，并在粘性候选失败后只向其后的 target 回退。
6. 让 Routing、Aggregation、Fallback 和普通 AI 可以通过类型化 upstream 边任意无环组合。
7. 在 Router-to-Router 边传播工具链所需的私有路由上下文，同时保证该上下文永不进入普通 AI 请求。
8. 保留未知公开请求字段、上游 SSE event 顺序和成功响应中的扩展字段。
9. 对失败信息做确定性、有限长度的 Socket 脱敏。
10. 同步 Python 入口、CLI、YAML、Gateway、持久化、REST/OpenAPI 与 SPA。
11. 保持 AnyIO 取消、`aclosing()`、单 choice 和 shielded cleanup 不变量。

## 3. 非目标

- 不对同一个 target 自动重试。
- 不实现权重、随机、轮询负载均衡或运行时顺序调整。
- 不实现健康检查、熔断、半开恢复或成功率统计。
- 不实现动态 Planner、任务拆分或候选子集选择。
- 不把失败候选的部分 content、reasoning、tool calls 或 compaction 帧输出给调用方。
- 不把多个成功候选综合为一个答案；这是 Aggregation 的职责。
- 不做磁盘暂存、跨进程粘性状态或 Router 会话历史持久化。
- 不自动发现原始 Socket 配置形成的拓扑，也不做运行时循环检测。
- 不允许 Selector/Aggregator 的专用 `router_socket` 充当模块组合边。
- 不修改 Session 的工具执行职责。

## 4. 术语

- **公开 Socket / `session_socket`**：一个 AI 或 Router 对上游暴露的 Chat Completions 端点。
- **控制 Socket / `router_socket`**：Routing 的 Selector AI 或 Aggregation 的 Aggregator AI。Fallback 没有控制 Socket。
- **target**：当前策略按配置调用的一个下游，可为 AI 或另一个 Router。
- **AI target**：`backend_type="ai"`，请求在进入它之前必须剥离 Router 私有上下文。
- **Router target**：`backend_type="router"`，请求可以携带 Router 内部组合上下文。
- **attempt**：Fallback 对一个 target 的一次完整缓冲调用。
- **可用结果**：具有实际完成终态，且包含非空白 content 或完整 tool calls 的响应。
- **工具迭代**：最后一条 message 的 `role` 为 `tool` 的请求。
- **粘性键**：`(routing.session_id, routing.path)`；用于隔离同一 Session 在组合图不同路径上的状态。
- **组合图**：以 Router/AI 为节点、类型化 upstream 为边的有向无环图。

### 4.1 `routing` 命名消歧

本文中的两个 `routing` 含义不同：

- `mode="routing"` / `RoutingStrategy` 是“让 Selector 选择一个 target”的 Router 模式；
- Chat Completions 请求体中的 `routing` 是 Session 写入的 Router 私有元数据容器。

当前 `SessionAgent` 在每轮请求中附加：

```json
{"routing": {"session_id": "stable-session"}}
```

这个字段不要求使用 Routing 模式，也不负责选择 target。Fallback 用它保存工具链候选，
Routing 用它保存 Selector 结果；本设计再增加内部 `path`，让所有嵌套 Router 的状态按调用
路径隔离。普通 AI 不需要该字段：Router 在 AI edge 前删除它，`psi_agent.ai` 的 provider
边界也继续把它剥离，形成第二道保护。

## 5. 核心设计决策

### 5.1 完整缓冲后提交

Fallback 不允许边接收边向 Session 转发。一个 target 可能已经生成部分文本后才断连、超时或返回 error；若这些文本已经提交，再切换到后续模型会把两个模型的答案混在同一 completion 中。

因此每次 attempt 必须：

1. 消费并验证完整 SSE stream；
2. 同时保存原始有效 events 和累积后的 completion；
3. 判断 completion 是否可用；
4. 仅在可用时原样重放已保存 events。

这会增加首字延迟和单个候选响应量级的内存占用，是可靠回退的必要代价。

### 5.2 共享缓冲原语

缓冲、单 choice 校验、finish reason 识别和 tool call 拼接属于共享传输职责，不应在 `FallbackStrategy` 复制 `RouterHttpClient.complete()`。

根包新增共享结果：

```python
@dataclass(frozen=True)
class BufferedCompletion:
    events: tuple[dict[str, Any], ...]
    completion: CompletionResult
```

`RouterHttpClient` 新增：

```python
async def buffered_complete(
    self,
    *,
    socket: str,
    body: dict[str, Any],
    **options: Any,
) -> BufferedCompletion: ...
```

`complete()` 委托 `buffered_complete()` 并仅返回 `.completion`，保持 Aggregation 与 Selector 的既有调用契约。`stream()` 仍是唯一底层 aiohttp/SSE 传输实现。

### 5.3 类型化组合边

仅凭 Socket 字符串无法判断下游是普通 AI 还是 Router。现有 `copy_public_request_body()` 会删除 `routing`；若父 Router 无条件删除，嵌套 Routing/Fallback 的工具链粘性会丢失；若无条件保留，Session 标识可能被传给模型 provider。

因此 `RouterTarget` 增加：

```python
backend_type: Literal["ai", "router"] = "ai"
```

转发规则为：

- AI target：删除 `model`、`routing`，强制 `stream=True`；
- Router target：删除 `model`，保留并规范化 `routing`，追加当前 target 的稳定路径段，强制 `stream=True`；
- Selector 和 Aggregator 控制 AI：始终按 AI target 处理，绝不接收 `routing`。

二元 upstream 配置继续默认 `backend_type="ai"`。指向另一个 Router 时必须显式使用三元配置或 Gateway 的 Router 引用。

### 5.4 任意无环组合

策略不得对相邻节点的具体 mode 写分支。一个 target 只需满足单 choice Chat Completions/SSE 契约，便可为 AI、Routing、Aggregation 或 Fallback。

合法示例包括但不限于：

```text
Routing -> Aggregation -> Fallback -> AI
Aggregation -> Routing -> Fallback -> AI
Fallback -> Aggregation -> Routing -> AI
Aggregation
|- Routing
|- Fallback
`- AI
```

组合关系只由 upstream 边表达。Session 连接组合图的根 Router。

## 6. 模块边界

目标结构：

```text
src/psi_agent/router/
├── entry.py              # 三模式 facade，只做显式 mode 组装
├── client.py             # stream / complete / buffered_complete
├── request.py            # AI/Router target 请求复制与私有上下文处理
├── privacy.py            # 共享私有 Socket 脱敏
├── models.py             # RouterMode/Target/Completion/BufferedCompletion
├── server.py             # mode-neutral HTTP/SSE 边界
├── routing/              # Selector + 工具链 sticky
├── aggregation/          # 全目标并发 + Aggregator
└── fallback/             # 顺序尝试 + 完整缓冲 + sticky
    ├── __init__.py
    ├── entry.py
    ├── errors.py
    ├── models.py
    └── strategy.py
```

共享传输、请求复制、错误脱敏和跨模式模型放根包。Fallback 特有的顺序、成功判定和状态只放在 `fallback/`。`server.py` 不允许出现任何 `RouterMode` 分支。

## 7. 公共模型与配置

### 7.1 `RouterMode`

```python
class RouterMode(StrEnum):
    ROUTING = "routing"
    AGGREGATION = "aggregation"
    FALLBACK = "fallback"
```

无效模式错误统一改为 `mode must be 'routing', 'aggregation', or 'fallback'`。

### 7.2 `RouterTarget`

```python
@dataclass(frozen=True)
class RouterTarget:
    candidate_id: str
    socket: str
    description: str
    backend_type: Literal["ai", "router"] = "ai"
```

校验保留 candidate ID 格式、非空 Socket/description，并新增严格 backend type 校验。

### 7.3 `FallbackConfig`

```python
@dataclass(frozen=True)
class FallbackConfig:
    session_socket: str
    targets: tuple[RouterTarget, ...] | list[RouterTarget]
    target_timeout: float | None = None
```

构造期校验：

- `session_socket` 为非空字符串；
- 至少一个 target；
- targets 全部为 `RouterTarget`；
- candidate ID 唯一；
- Socket 唯一；
- target Socket 不直接等于 `session_socket`；
- timeout 为 `None` 或有限正数，拒绝 bool、0、负数、NaN 和无穷。

`targets` 在 frozen 对象中规范化为 tuple。

### 7.4 独立入口

```python
@dataclass
class FallbackRouter:
    session_socket: str
    targets: list[RouterTarget]
    target_timeout: float | None = None
    verbose: bool = False

    async def run(self) -> None: ...
```

`run()` 第一行可执行语句必须是 `setup_logging(verbose=self.verbose)`，随后构造 `FallbackConfig`、`RouterHttpClient`、`FallbackStrategy` 并调用共享 `serve_router()`。

### 7.5 统一 `Router`

统一入口使用明确的配置别名：

```python
type RouterUpstream = tuple[str, str] | tuple[str, str, Literal["ai", "router"]]
```

`Router.router_socket` 的类型改为 `str | None`，`Router.upstream` 的类型改为
`list[RouterUpstream]`。另增加仅供统一入口/CLI 使用的
`upstream_types: list[Literal["ai", "router"]]`，默认空列表；两个核心字段继续是显式构造
参数，因此不会给 Routing/Aggregation 悄悄选择控制 AI。

统一 facade 支持：

```python
Router(
    session_socket=...,
    router_socket=...,  # fallback 传 None
    mode="fallback",
    upstream=[
        ("./primary.sock", "primary"),
        ("./nested.sock", "nested router", "router"),
    ],
    upstream_types=[],
    router_timeout=None,
    target_timeout=60,
    max_context_chars=12_000,
)
```

兼容规则：

- 二元 `(socket, description)` 规范化为 AI target；
- 三元 `(socket, description, backend_type)` 支持显式 Router target；
- 非空 `upstream_types` 必须与 upstream 等长，且此时 upstream 必须全部为二元组；按相同
  索引覆盖二元组的默认 AI 类型；
- 三元 upstream 与非空 `upstream_types` 不得混用，避免两个类型来源冲突；
- 其他长度、非字符串值或未知 backend type 在启动任何服务前失败；
- Routing/Aggregation 要求非空 `router_socket`；
- Fallback 要求 `router_socket is None`；
- `router_timeout` 和 `max_context_chars` 在 fallback 模式不参与运行，推荐分别传 `None` 和默认值；
- `target_timeout` 在三种模式中保持每个 target 调用的总超时。

## 8. 私有路由上下文

### 8.1 结构

Session 现有入口字段继续使用：

```json
{
  "routing": {
    "session_id": "session-a"
  }
}
```

Router-to-Router 转发时规范化为：

```json
{
  "routing": {
    "session_id": "session-a",
    "path": ["candidate-2", "candidate-1"]
  }
}
```

`path` 是 Router 内部扩展：

- 缺省视为空列表；
- 必须为符合 candidate ID 规则的字符串列表；
- 每跨过一条 Router target 边，追加该边的 `candidate_id`；
- 不包含 Socket、mode、描述或模型内容；
- 不写入 Session conversation history；
- 在进入 AI target、Selector 或 Aggregator 前删除。

### 8.2 粘性作用域

Routing 与 Fallback 的状态 key 从裸 `session_id` 扩展为：

```python
type RoutingScopeKey = tuple[str, tuple[str, ...]]
```

这样同一 Session 在组合图不同分支、不同层级或共享子 Router 上的状态不会互相覆盖。没有 `session_id` 时不保存状态；没有 path 时使用空 tuple。

### 8.3 请求复制 API

`request.py` 保留 AI 公共复制语义，并增加明确的 target-aware helper：

```python
def copy_public_request_body(*, body: dict[str, Any]) -> dict[str, Any]: ...


def copy_target_request_body(
    *,
    body: dict[str, Any],
    target: RouterTarget,
) -> dict[str, Any]: ...
```

`copy_target_request_body()` 在 Router edge 上把 `target.candidate_id` 追加到复制体的
`routing.path`。两者都深拷贝、删除 `model`、强制 `stream=True`、保留其他未知公开字段，
且绝不修改 caller dict。

## 9. `buffered_complete()` 协议

`buffered_complete()` 必须复用 `stream()` 并经 `aclosing()` 消费。它执行：

1. 跳过 0 choice 心跳；
2. 拒绝多 choice；
3. 验证 choice/delta/finish reason 类型；
4. 按顺序保存每个有效 event，不重建或丢弃未知顶层字段；
5. 拼接 string content 与 reasoning；
6. 按 tool call index 累积 id/type/function name/arguments；
7. 把 `compaction_needed` 视为辅助帧，不覆盖真实 completion finish；
8. 把 `finish_reason="error"` 转换为 `RouterUpstreamError`；
9. 流结束仍无实际 finish reason 时失败；
10. 验证 `tool_calls` 终态与结构完整性；
11. 返回不可变 event tuple 和 `CompletionResult`。

`complete()` 只取 `.completion`。现有 Aggregation 对 reasoning 的舍弃和工具材料语义不变。

## 10. Fallback 算法

### 10.1 请求起点

1. 复用共享 Server 请求校验。
2. 读取并规范化 `(session_id, path)`。
3. 判断最后一条 message 是否为 tool。
4. 普通用户轮次清除该 scope 的旧状态，从索引 0 开始。
5. 工具迭代且命中 sticky 时，从保存索引开始。
6. 工具迭代但无 sticky 时，从索引 0 开始。

### 10.2 顺序尝试

对 `targets[start_index:]` 严格串行：

1. 按 target 类型构造独立请求副本；
2. 调用 `buffered_complete(socket=..., timeout=target_timeout)`；
3. 普通失败记录脱敏摘要并继续；
4. 取消异常立即传播，不进入下一 target；
5. completion 不可用时视为普通失败；
6. 首个可用 completion 停止循环。

任何时刻只允许一个 attempt 处于运行状态。

### 10.3 成功判定

必须同时满足：

- 收到一个非 `compaction_needed` 的实际 finish reason；
- finish reason 不是 `error`；
- `content.strip()` 非空，或 `tool_calls` 非空且结构完整。

允许的成功包括 `stop`、`tool_calls`、`length`、`content_filter` 及框架可接受的其他非 error 字符串终态，只要有可用 content/tool calls。reasoning-only 不算成功。

### 10.4 成功重放

成功后按原顺序 yield `BufferedCompletion.events`：

- 不合并 chunk；
- 不重写 id、model、usage、`psi_compaction` 或其他扩展字段；
- 保留成功候选的 reasoning、content、tool calls 和 compaction 辅助帧；
- 不包含任何失败候选 event。

若重放阶段下游断连或取消，不启动新 attempt。

### 10.5 全部失败

全部剩余 target 失败时：

1. 清除该 scope sticky；
2. 抛出 `FallbackError`；
3. 公共 Server 将其转换为一个 `[Router Error]: ...` SSE frame，`finish_reason="error"`；
4. 随后不发送正常完成帧。

## 11. 工具调用与粘性

### 11.1 保存与继续

若成功候选以 `finish_reason="tool_calls"` 返回完整 tool calls：

- 有 scope key 时保存该候选索引；
- Session 执行工具；
- 下一次 tool 迭代从同一索引开始。

若该候选在工具迭代失败，只尝试配置中更靠后的候选，不回绕到更早候选。后续候选若再次返回 `tool_calls`，sticky 更新为新索引。

### 11.2 清理

以下情况清除对应 scope：

- 成功终态不是 `tool_calls`；
- 所有剩余候选失败；
- attempt 或重放未正常完成；
- 客户端断连；
- 同一 scope 出现新的非 tool 用户轮次；
- `discard(session_id)`；
- Router shutdown 的 `clear()`。

`discard(session_id)` 必须删除该 session 的所有 path scope；`clear()` 删除全部 scope。

### 11.3 嵌套工具链

Router-to-Router 边保留 session ID 并追加 path，因此每一层 Routing/Fallback 都能独立保持自己的候选。Aggregation 仍无状态，但对每个 Router target 使用不同路径段。

## 12. 失败分类与输出

| 条件 | 当前 attempt | 是否继续下一个 | 是否对外输出已缓冲事件 |
|---|---|---:|---:|
| 非 200 | 失败 | 是 | 否 |
| 连接失败或 target timeout | 失败 | 是 | 否 |
| 畸形 SSE/JSON | 失败 | 是 | 否 |
| 多 choice | 失败 | 是 | 否 |
| `finish_reason="error"` | 失败 | 是 | 否 |
| 无实际 finish reason | 失败 | 是 | 否 |
| reasoning-only 或空结果 | 失败 | 是 | 否 |
| 完整 content | 成功 | 否 | 原样重放 |
| 完整 tool calls | 成功 | 否 | 原样重放 |
| 调用方取消 | 取消 | 否 | 否 |
| 重放时客户端断连 | 终止 | 否 | 已写部分不可撤销 |

嵌套错误的外层行为：

- 外层 Fallback：子 Router error 是一个失败 attempt，继续下一 target；
- 外层 Aggregation：子 Router error 是一个失败分支，其他分支继续；
- 外层 Routing：选中目标失败后直接返回 error，不增加 Routing 自己的 fallback。

## 13. 隐私、日志与错误摘要

新增根包共享脱敏 helper，把 Aggregation 当前内联逻辑移入单一实现：

```python
def redact_private_sockets(
    *,
    text: str,
    sockets: Collection[str],
    limit: int = 512,
) -> str: ...
```

处理顺序按表示长度降序，替换：

- 原始 Socket；
- `repr(socket)`；
- 去掉引号的 repr/转义表示。

替换值固定为 `<private-socket>`。每个 attempt 摘要最多 512 字符。

`FallbackError` 的外部文本按配置顺序包含：

- candidate ID；
- 异常类型；
- 脱敏摘要。

不得包含真实 Socket、原始请求、API key、reasoning 或 Python traceback。INFO 日志只记录 candidate ID/description/status；每个传输边界 chunk 继续写 DEBUG。

## 14. 取消、资源和内存

- 所有 async generator 经 `aclosing()` 消费。
- attempt 失败、成功停止、异常或取消都必须关闭当前上游 stream。
- AnyIO cancellation 不得被 `except Exception` 转成候选失败；必须继续传播。
- aiohttp session/response/runner 清理维持 shielded CancelScope。
- Fallback 不创建 task group；attempt 严格串行。
- 内存只保留当前 attempt 的 events 和 completion 累积；失败后释放再开始下一 attempt。
- 不把所有失败候选的完整响应留在内存，只保留有限错误摘要。

## 15. 组合与 timeout

每一层独立应用自己的 target timeout。内层 Fallback 的最坏执行时间近似为：

```text
剩余候选数 × target_timeout + 协议/调度开销
```

外层 Router 的 target timeout 若更短，会取消整个内层调用。系统不自动推导或放大 timeout；CLI、YAML 和 SPA 文档必须提示操作者按组合深度配置。

原始 Socket 配置允许任意无环组合，但不做间接环检测。Gateway 通过“只能引用已存在 Router、Router 不支持修改依赖、被依赖 Router 不可删除”的规则，使正常 API 只能从叶到根构造 DAG。

## 16. CLI、YAML 与 Python

### 16.1 YAML

二元 upstream 默认 AI；三元 upstream 的第三项为 backend type：

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
```

该配置表达 `Aggregation -> Routing -> Fallback`。改变 upstream 引用即可表达其他顺序。

### 16.2 CLI

CLI 继续由 tyro 暴露统一 Router dataclass。文档示例必须说明：

- fallback 的 `router_socket` 使用 None 表达；
- `--upstream` 继续使用现有的二元 Socket/description 序列；
- 组合时用同序 `--upstream-types` 提供每个 target 的 `ai` 或 `router`，例如
  `--upstream a.sock primary child.sock nested --upstream-types ai router`；
- 省略 `--upstream-types` 时全部 target 默认为 AI；
- shell quoting 继续遵守 Windows Named Pipe 规则。

### 16.3 Python

推荐简单 Fallback 使用 `FallbackRouter`，多模式配置系统使用统一 `Router`。所有公开示例使用关键字参数。

## 17. Gateway、状态、REST 与 SPA

### 17.1 Gateway 模型

```python
@dataclass(frozen=True)
class RouterUpstreamInfo:
    backend_type: Literal["ai", "router"]
    backend_id: str
    description: str


@dataclass(frozen=True)
class RouterInfo:
    ...
    mode: str
    router_ai_id: str | None
    upstreams: tuple[RouterUpstreamInfo, ...]
    ...
```

规则：

- Routing/Aggregation：`router_ai_id` 必须引用现有 AI；
- Fallback：`router_ai_id` 必须为 `None`；
- AI upstream 通过 `AIManager.get_socket()` 解析；
- Router upstream 通过 `RouterManager.get_socket()` 解析；
- upstream 只能引用创建时已存在的 backend；
- duplicate 以 `(backend_type, backend_id)` 判断；
- 删除 Router 前扫描活动 Router 依赖，被引用时返回冲突错误；
- Selector/Aggregator 不允许引用 Router。

### 17.2 State 单向迁移

旧 upstream：

```json
{"ai_id": "model-a", "description": "general"}
```

加载后规范化为：

```json
{"backend_type": "ai", "backend_id": "model-a", "description": "general"}
```

迁移只发生在内存；加载不立即覆写源文件。下一次正常 save 只写新格式。新 REST API 不接受旧 `ai_id` 别名。

Fallback state 的 `router_ai_id` 保存为 JSON null。恢复必须按持久化创建顺序进行；正常 API 保证依赖先于使用者。缺失或无效依赖记录 warning 并跳过该 Router，不自动改写引用。

### 17.3 REST/OpenAPI

Router create/list schema：

```json
{
  "name": "resilient route",
  "mode": "fallback",
  "router_ai_id": null,
  "upstreams": [
    {"backend_type": "ai", "backend_id": "primary", "description": "primary"},
    {"backend_type": "router", "backend_id": "nested", "description": "nested router"}
  ],
  "router_timeout": null,
  "target_timeout": 60,
  "max_context_chars": 12000
}
```

OpenAPI mode enum 增加 fallback，backend type enum 为 ai/router，并用 mode-aware `oneOf` 表达 `router_ai_id` 条件。删除被引用 Router 返回 HTTP 409，而不存在仍返回 404。

### 17.4 SPA

- mode 增加 Fallback；
- Routing 显示 Selector AI，Aggregation 显示 Aggregator AI，Fallback 隐藏并提交 null；
- 每行 upstream 先选 AI/Router 类型，再从对应现有资源中选 backend；
- 当前正在创建的 Router 不出现在 Router upstream 列表；
- 列表显示 mode、控制 AI（若有）、upstream 类型/名称和 timeout；
- UI 按叶到根创建，不引入图编辑器或自动排序。

### 17.5 标题与摘要

现有 Router-backed Session 的标题/摘要解析保持：

- Routing/Aggregation 使用专用 `router_ai_id`；
- Fallback 没有控制 AI，因此调用 Fallback 自己的公开 Router Socket，让相同回退策略生成标题/摘要。

## 18. 兼容性

- 现有 `mode="routing"` 与 `mode="aggregation"` 行为不得改变。
- 现有二元 upstream 被解释为 AI target，无需修改。
- `CompletionResult` 字段和 `RouterHttpClient.complete()` 返回类型不变。
- `copy_public_request_body()` 对 AI/control AI 的剥离行为不变。
- Router-to-Router 上下文传播只在显式 `backend_type="router"` 时开启。
- Gateway 旧 state upstream 做单向迁移；REST/OpenAPI/SPA 只暴露新格式。
- 不恢复已删除的 `RouterClient`、`UpstreamResult`、`stream_raw` 或 `Orchestrator` API。
- Router error 仍使用内部扩展 `finish_reason="error"`。

## 19. 文件变更范围

### Router 核心

- 修改 `src/psi_agent/router/models.py`
- 修改 `src/psi_agent/router/client.py`
- 修改 `src/psi_agent/router/request.py`
- 新建 `src/psi_agent/router/privacy.py`
- 修改 `src/psi_agent/router/entry.py`
- 修改 `src/psi_agent/router/__init__.py`
- 修改 `src/psi_agent/router/routing/models.py`
- 修改 `src/psi_agent/router/routing/strategy.py`
- 修改 `src/psi_agent/router/aggregation/strategy.py`
- 新建 `src/psi_agent/router/fallback/__init__.py`
- 新建 `src/psi_agent/router/fallback/entry.py`
- 新建 `src/psi_agent/router/fallback/errors.py`
- 新建 `src/psi_agent/router/fallback/models.py`
- 新建 `src/psi_agent/router/fallback/strategy.py`

### 入口与 Gateway

- 修改 `src/psi_agent/_run.py`
- 修改 Router 相关 CLI 测试；`src/psi_agent/cli.py` 预计无需行为分支
- 修改 `src/psi_agent/gateway/_router_manager.py`
- 修改 `src/psi_agent/gateway/_state.py`
- 修改 `src/psi_agent/gateway/__init__.py`
- 修改 `src/psi_agent/gateway/server.py`
- 修改 `src/psi_agent/gateway/_openapi.py`
- 修改 Gateway SPA Router store/config/dialog/list组件

### 测试与文档

- 新建 `tests/psi_agent/router/fallback/` package 及测试
- 修改共享 Router、Routing、Aggregation、CLI/YAML 与 Gateway 测试
- 新建组合契约及真实 Session 集成测试
- 修改 `src/psi_agent/router/README.md`
- 修改 `src/psi_agent/router/AGENTS.md`
- 修改根 `AGENTS.md`、Gateway/SPA 相关 AGENTS 文档

## 20. 测试策略

### 20.1 模型与请求复制

- FallbackConfig 的所有合法/非法边界；
- RouterTarget backend type；
- 二元 AI upstream 与三元 AI/Router upstream 规范化；
- AI edge 删除 routing；
- Router edge 保留 session ID、验证/追加 path；
- caller body 深拷贝且未知字段透传；
- 相同路径跨工具轮稳定，不同分支路径隔离。

### 20.2 缓冲客户端

- 原始 event 顺序和未知扩展字段保留；
- content/reasoning/tool calls 累积；
- compaction 辅助帧不覆盖真实终态；
- 0/multi choice；
- error、无终态、畸形 JSON/SSE；
- 提前退出与取消关闭 stream；
- `complete()` 委托后行为不回归。

### 20.3 FallbackStrategy

- 第一候选成功时不调用后续候选；
- 第一候选各种失败后调用第二候选；
- 严格串行而非并发；
- reasoning-only/空响应失败；
- content/tool calls 成功；
- 成功 events 原样重放，失败 events 完全丢弃；
- 所有失败产生有序、脱敏、有限摘要；
- 取消不被隔离；
- 重放断连不重试；
- tool sticky、向后回退、不回绕、更新与清理；
- `discard(session_id)` 清除该 Session 全部 path。

### 20.4 任意组合

用参数化测试覆盖外层 `{routing, aggregation, fallback}` × 内层 `{routing, aggregation, fallback}` 的 3×3 协议兼容矩阵。另用真实 Server/Session 覆盖三个模块的六种排列，并覆盖至少一个分支组合图。

组合测试必须验证：

- Router edge 传播私有 scope；
- AI edge 不收到 routing；
- 嵌套 tool chain 粘性；
- 子 Router error 在外层策略中的正确解释；
- timeout 和取消逐层传播；
- 最终只有一个合法单 choice SSE 流到 Session。

### 20.5 Gateway 与 SPA

- mode/字段条件校验；
- AI/Router backend 解析；
- 只能引用现有 Router；
- 依赖删除返回 409；
- 旧 state 迁移且 load 不改文件；
- save/REST/OpenAPI 只使用新格式；
- Fallback 标题/摘要 Socket；
- SPA payload、动态字段、资源筛选、显示和构建。

## 21. 验证命令

实施完成后至少运行：

```text
uv run pytest -q tests/psi_agent/router
uv run pytest -q tests/psi_agent/gateway/test_router_manager.py tests/psi_agent/gateway/test_state.py tests/psi_agent/gateway/test_server.py tests/psi_agent/gateway/test_openapi.py
uv run pytest -q tests/integration/test_serial_multi_ai_router.py tests/integration/test_fallback_router_composition.py tests/integration/test_gateway.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

在 `src/psi_agent/gateway/spa`：

```text
npm test -- --run
npm run build
```

隐私与旧契约扫描：

```text
rg -n "ai_id|max_context_length|default_ai_id" src/psi_agent/gateway tests/psi_agent/gateway src/psi_agent/gateway/spa/src
rg -n "fallback.*not supported|不支持.*fallback" AGENTS.md src/psi_agent/router
```

第一条允许 state 迁移代码和明确验证迁移的测试命中，其他生产 REST/Manager/SPA 契约不得继续使用旧 upstream `ai_id`。

## 22. 验收标准

1. Fallback 严格按配置顺序一次只调用一个 target。
2. 失败 target 的任何 event 都不会到达调用方。
3. 首个具有非空白 content 或完整 tool calls 的有效完成被原样重放。
4. 全部 target 失败时只产生一个不泄漏 Socket 的 Router SSE error。
5. 工具结果轮从成功候选继续，失败后只向后回退且不回绕。
6. Routing/Fallback sticky 由 `(session_id, path)` 隔离。
7. AI target 永不接收 `routing`；Router target 能获得稳定内部 scope。
8. 三个 Router mode 可以按任意顺序组成无环调用图，不存在固定 pipeline 分支。
9. 3×3 相邻模式矩阵、六种三层排列和分支图集成测试通过。
10. Gateway/SPA 能从叶到根引用 AI 或已有 Router，并阻止删除被依赖 Router。
11. routing/aggregation 的现有行为、二元 upstream、`complete()` 与公共 Server 协议不回归。
12. 全量测试、ruff、format、ty、SPA tests/build 全部通过。
