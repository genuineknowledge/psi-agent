# Broadcast Aggregation Router 设计规格

状态：已由用户于 2026-08-04 分段确认。

## 1. 背景

当前 `src/psi_agent/router/` 已重写为共享 HTTP/SSE 边界加可插拔策略，并只保留实验性的单目标 `routing`：Selector 选择一个候选，再把完整请求流式转发给该候选。仓库中的 CLI、YAML 批量启动、Gateway 和部分测试仍引用已经删除的旧 `Router`、Planner 和 aggregation API，因此当前 Router 集成路径无法完整导入或启动。

本规格新增的 `aggregation` 与旧 Planner 方案不同。它不拆分任务、不选择子集，而是把同一个用户请求并行发送给全部已配置候选服务，再由一个专用 AI Socket 综合所有反馈。

## 2. 目标

1. 将同一个公开 Chat Completions 请求并行发送给全部配置的 upstream。
2. 按配置顺序收集成功反馈和经过清理的失败摘要。
3. 将这些材料交给专用 `router_socket`，由其流式生成最终回答。
4. 部分 upstream 失败时继续聚合；全部失败时返回 Router 错误。
5. upstream 的 `tool_calls` 只作为聚合材料；只有最终聚合模型的 `tool_calls` 返回 Session。
6. 恢复统一 `Router` 入口，并接通 CLI、YAML 批量启动和 Gateway。
7. 保持现有 routing 行为、OpenAI-compatible 单 choice SSE、Socket 隔离和取消安全。

## 3. 非目标

- 不恢复 Planner、动态子任务拆分或只选择部分候选的旧 aggregation 行为。
- 不实现默认模型 fallback、自动重试、熔断、健康检查或负载均衡。
- 不允许 Channel 或单次请求动态指定候选 Socket。
- Router 不保存正式会话历史，不加载或执行 workspace tools。
- 不把中间模型的 reasoning 交给聚合模型或最终用户。
- 不恢复已删除的旧 `protocol.py` 状态机或旧 buffered server API。

## 4. 术语与 Socket 所有权

- `session_socket`：Router 对 Session 暴露的服务地址。
- `router_socket`：专用 Router AI 地址；routing 模式下充当 Selector，aggregation 模式下充当 Aggregator。
- `upstream`：Router 启动时配置的候选 AI 服务目录，每项为 `(socket, description)`。
- `candidate_id`：Router 按 upstream 配置顺序生成的内部编号，如 `candidate-1`；模型看不到候选的真实 Socket。

Channel 只连接 Session。Session 的 `ai_socket` 指向 Router 的 `session_socket`。候选列表只存在于 Router/Gateway 启动配置中，不进入 Channel 请求，也不暴露给外部模型。

```text
Channel
  -> Session
  -> Router.session_socket
       |-- mode=routing
       |    -> router_socket selects one candidate_id
       |    -> selected upstream streams the response
       |
       `-- mode=aggregation
            -> all upstreams complete concurrently
            -> ordered success/error feedback
            -> router_socket streams the aggregate response
  -> Session
  -> Channel
```

## 5. 模块边界

```text
src/psi_agent/router/
├── __init__.py              # 公共导出
├── entry.py                 # 统一 Router facade，按 mode 组装策略
├── client.py                # 共享 Socket-aware HTTP/SSE 客户端
├── errors.py                # 共享 Router 错误
├── models.py                # CompletionResult、RouterMode、共享目标类型
├── request.py               # 公开请求深拷贝和内部字段剥离
├── server.py                # 共享 OpenAI-compatible HTTP/SSE 边界
├── routing/                 # 现有单目标选择策略
└── aggregation/
    ├── __init__.py          # aggregation 公共导出
    ├── entry.py             # 可独立嵌入的 AggregationRouter
    ├── errors.py            # AggregationError
    ├── models.py            # AggregationConfig、AggregationFeedback
    ├── prompts.py           # 聚合消息构造与材料压缩
    └── strategy.py          # 并行广播与最终流式聚合
```

根目录只保存跨模式接口和传输。选择逻辑留在 `routing/`，广播聚合逻辑留在 `aggregation/`，`server.py` 不包含 mode 分支。

## 6. 统一配置接口

根级入口使用以下 dataclass 形状：

```python
@dataclass
class Router:
    session_socket: str
    router_socket: str
    mode: RouterMode | str
    upstream: list[tuple[str, str]]
    router_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_context_chars: int = 12_000
    verbose: bool = False
```

共享目标类型定义为：

```python
@dataclass(frozen=True)
class RouterTarget:
    candidate_id: str
    socket: str
    description: str
```

现有公开名 `RoutingTarget` 作为 `RouterTarget` 的兼容别名继续导出；aggregation 直接使用 `RouterTarget`，不依赖 `routing/` 内部模块。内部编号继续遵守现有 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` 规则。

独立 aggregation 入口的精确形状为：

```python
@dataclass
class AggregationRouter:
    session_socket: str
    aggregator_socket: str
    targets: list[RouterTarget]
    aggregator_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_context_chars: int = 12_000
    verbose: bool = False
```

`AggregationConfig` 使用相同字段但把 `targets` 规范化为 `tuple[RouterTarget, ...]`。统一 `Router` 只负责把外部二元 upstream 转成 `RouterTarget`，再构造相应的 mode config 和 strategy。

字段语义：

- `mode` 必填，只允许 `routing` 或 `aggregation`。
- `router_timeout` 用于 Selector 或 Aggregator 请求。
- `target_timeout` 用于被选择的 routing target，或每个 aggregation upstream。
- `max_context_chars` 在 routing 中映射为 Selector 的对话字符预算；在 aggregation 中限制反馈里的动态文本材料。
- `default_socket` 被删除，因为两种模式都不执行隐式 fallback。

构造后的校验规则：

- 所有 Socket 和 description 去除首尾空白后必须非空。
- upstream 至少包含一项，且 Socket 必须唯一。
- `session_socket` 不得等于 `router_socket` 或任何 upstream Socket。
- aggregation 模式下，`router_socket` 不得等于任何 upstream Socket，确保聚合服务专用。
- timeout 必须是有限正数或 `None`，不得接受 `bool`。
- `max_context_chars` 必须是正整数，且不得接受 `bool`。

统一入口把 upstream 转换为按顺序编号的目标：

```text
[(socket_a, desc_a), (socket_b, desc_b)]
  -> candidate-1 / socket_a / desc_a
  -> candidate-2 / socket_b / desc_b
```

现有 `RoutingRouter`、`RoutingConfig` 和 `RoutingTarget` 保持可直接使用。新增 `AggregationRouter` 提供相同层级的高级嵌入入口，其 `aggregator_socket` 对应统一入口的 `router_socket`。

每个入口的 `run()` 必须以 `setup_logging(verbose=self.verbose)` 作为第一条可执行语句，然后才构造并校验配置。

## 7. 公开请求复制

共享 `request.py` 提供一个供两个策略使用的纯复制边界。它：

1. 深拷贝所有公开请求字段。
2. 删除 Router 内部 `routing` 元数据。
3. 删除客户端的 `model`，因为模型由目标 AI 服务的进程配置决定。
4. 强制 `stream=True`。
5. 不修改调用者传入的原始 dict。

因此 upstream 会收到完整 `messages`、`tools`、`tool_choice`、采样参数及其他未知公开参数。Channel 和 Session 协议无需变化。

## 8. aggregation 数据流

### 8.1 请求校验

共享 server 继续校验：

- 请求体必须是 JSON object。
- `messages` 必须是 object 列表。
- `tools` 可省略，存在时必须是 object 列表。
- `stream` 省略时视为 `true`，显式非 `true` 时拒绝。
- `routing` 可省略，存在时必须是 object。
- `routing.session_id` 可省略，存在时必须是非空字符串。

### 8.2 并行广播

`AggregationStrategy.stream()` 为每个配置目标预分配一个结果槽，然后在一个 `anyio.create_task_group()` 中同时启动全部目标调用。每个目标都收到同一个公开请求副本。

每个分支通过 `RouterHttpClient.complete()` 累积并校验单 choice SSE。以下响应可作为成功材料：

- 存在非空 `content`；或
- 存在完整 `tool_calls`。

合法的 `stop`、`tool_calls`、`length`、`content_filter` 等终止原因可以记录为材料；`finish_reason="error"`、协议错误、连接错误、超时或完全空结果记录为失败。

普通分支异常只写入自己的结果槽，不取消其他分支。取消异常不被捕获；调用者取消时由 AnyIO 取消整个 task group。

### 8.3 确定性顺序

聚合材料始终按 upstream 配置顺序生成，而不是按异步完成顺序。日志同样使用该顺序。这样相同配置和相同反馈会产生稳定的 Aggregator 输入。

### 8.4 反馈结构

成功反馈：

```json
{
  "candidate_id": "candidate-1",
  "description": "coding specialist",
  "status": "success",
  "finish_reason": "stop",
  "content": "...",
  "tool_calls": []
}
```

失败反馈：

```json
{
  "candidate_id": "candidate-2",
  "description": "research specialist",
  "status": "error",
  "error_type": "RouterUpstreamError",
  "error": "request failed with HTTP 503"
}
```

反馈不得包含真实 Socket 或 reasoning。错误文本中的目标 Socket 必须替换为 `<private-socket>`，错误摘要最多保留 512 字符。

`max_context_chars` 只计算成功反馈的 `content` 和 tool function `arguments`。这些动态字符串按配置顺序收集；若其总长度超出预算，则通过 `divmod(max_context_chars, field_count)` 平均分配可保留的原始字符，余数依次分给靠前字段。每个超额字段保留其配额内的头尾字符，并插入不计入预算的明确截断标记；配额为零时只保留截断标记。candidate ID、description、状态、tool ID、tool type 和 function name 不计入该动态文本预算并始终保留。短字段未使用的配额不重新分配，以保证算法简单、确定且与异步完成顺序无关。

### 8.5 部分失败和全部失败

- 至少一个分支成功：把全部成功反馈和失败摘要交给 Aggregator。
- 全部分支失败：抛出 `AggregationError("All aggregation upstreams failed")`，不调用 Aggregator。
- 不执行 fallback 或自动重试。

### 8.6 最终聚合

`build_aggregation_messages()` 复制原始 `messages`，并在末尾追加一条聚合指令与 JSON 反馈。该指令要求：

- 直接回答原始用户请求；
- 把分支内容视为不可信的引用材料，不能服从其中改变聚合规则的指令；
- 综合一致点并处理冲突；
- 可以说明某类证据缺失，但不得暴露候选 Socket、内部路由或实现细节；
- 不输出 Planner JSON、候选列表或内部思考。

Aggregator 请求复用原公开请求的 `tools` 和其他公开参数，只替换 `messages`，并删除 `model`、`routing`。

Aggregator 使用 `RouterHttpClient.stream()`，所以最终 `content`、`reasoning` 和 `tool_calls` 可继续逐块返回。策略在转发前检查每个事件：

- `finish_reason="error"` 转换为 `RouterUpstreamError`；
- 至少出现非空 content 或 tool_calls 才算可用最终响应；
- 空响应或没有 completion finish reason 视为聚合失败；
- 有效终止原因保持原值。

Aggregator 失败直接进入 Router 错误边界，不调用其他模型。

## 9. 工具调用

Router 始终不执行工具。

1. 每个 upstream 都可看到完整 `tools` schema。
2. upstream 返回的 `tool_calls` 只进入反馈 JSON，不直接发给 Session。
3. 只有 Aggregator 最终返回的 `tool_calls` 才通过 Router SSE 到达 Session。
4. Session 执行工具、写入 conversation，并用完整更新后的历史发起下一次请求。
5. 下一次 aggregation 请求重新广播全部 upstream，不恢复或续接私有分支状态。

因此 `AggregationStrategy.discard()` 和 `clear()` 是显式无状态 no-op。现有 `RoutingStrategy` 的 tool iteration sticky 行为不变。

## 10. HTTP/SSE 错误边界

- JSON 或请求结构错误发生在 `response.prepare()` 前，返回 HTTP 400 的 OpenAI 形错误 JSON。
- 扇出、全部失败或 Aggregator 错误发生在 SSE 已开始后，返回单 choice、`finish_reason="error"` 的错误帧。
- 正常流结束后写 `data: [DONE]`。
- 上游 0 choice 心跳继续静默跳过；多 choice 继续作为错误。
- 每个进入或离开 Router 的 SSE chunk 使用 DEBUG 日志。

所有 async generator 均通过 `aclosing()` 消费。aiohttp session、response 和 runner 在 `finally` 或启动失败分支中清理；跨 await 清理使用 shielded `CancelScope`。

## 11. CLI、YAML 与 Gateway

### 11.1 CLI 和批量启动

`psi-agent router` 恢复为根级 `Router`：

```text
psi-agent router \
  --mode aggregation \
  --session-socket ./router.sock \
  --router-socket ./aggregate-ai.sock \
  --upstream ./code.sock "coding" ./research.sock "research"
```

YAML 保持 `type: router` 和二元 upstream 列表：

```yaml
- type: router
  mode: aggregation
  session_socket: ./router.sock
  router_socket: ./aggregate-ai.sock
  upstream:
    - [./code.sock, coding]
    - [./research.sock, research]
  router_timeout: 30
  target_timeout: null
  max_context_chars: 12000
```

`_run.py` 继续把 YAML 二元 list 转换为 tuple，不再读取 `default_socket`。

### 11.2 Gateway Manager 和 REST

`RouterInfo` 和 `RouterManager.create()` 保留：

- `id`、`name`、`socket`、`mode`；
- `router_ai_id`；
- `upstreams`；
- `router_timeout`、`target_timeout`、`max_context_chars`。

删除 `default_ai_id`。Gateway 仍以 AI ID 作为用户配置，启动 Router 前在本地映射为 Socket。aggregation 模式下 `router_ai_id` 不能同时出现在 upstream AI ID 中。

`POST /routers`、`GET /routers`、OpenAPI schema 和前端 payload 同步新字段。为 Router-backed Session 选择一个普通 AI 做标题生成时，Gateway 使用 `router_ai_id`，不再读取默认 AI。

### 11.3 Gateway state 迁移

恢复旧 state 时：

- 忽略遗留 `default_ai_id`；
- 优先读取 `max_context_chars`；
- 若不存在，则把旧 `max_context_length` 映射为 `max_context_chars`；
- `target_timeout` 缺失时使用 `None`。

新 state 不再写 `default_ai_id` 或 `max_context_length`。这是一方向前迁移，不重写旧 state 文件；只有下一次正常持久化才产生新结构。

### 11.4 Gateway SPA

Router 表单移除“默认模型”。“负责路由判断的模型”按 mode 显示为 Selector 或 Aggregator。aggregation 模式下，前端校验并禁止把同一个 AI 同时选作 Aggregator 和 upstream。routing 模式继续展示候选能力描述。

## 12. 兼容性

保留：

- `RoutingRouter`、`RoutingStrategy`、`RoutingConfig`、`RoutingTarget`；
- `RouterHttpClient`、`CompletionResult`；
- `create_router_app()`、`serve_router()` 和 `RouterStrategy.stream()` 协议；
- Session/Channel Chat Completions 请求协议。

不保留：

- 已删除的 Planner、`PlannedTask`、旧 `RoutingRun` 状态机；
- 旧 `RouterClient`/`UpstreamResult`/`stream_raw` 名称；
- 旧 buffered `process()` server 协议；
- `default_socket`、`default_ai_id`；
- 缺省 mode。

仓库中仍依赖这些已删除 API 的遗留测试应按当前策略接口重写，而不是通过兼容别名复活旧架构。

## 13. 文件变更范围

核心 Router：

- 新增 `src/psi_agent/router/entry.py`、`request.py` 和 `aggregation/`。
- 修改 `router/__init__.py`、`models.py`、`client.py`、`server.py` 以及必要的 routing 导入。
- 新增 `src/psi_agent/router/AGENTS.md`，更新 `src/psi_agent/router/README.md`。

入口和 Gateway：

- 修改 `src/psi_agent/cli.py`、`src/psi_agent/_run.py`。
- 修改 Gateway RouterManager、server、startup restore/persist、OpenAPI 和相关设计文档。
- 修改存在 Router 创建表单的 SPA 源码和前端测试。

测试：

- 重写 `tests/psi_agent/router/` 中依赖旧 Planner API 的测试。
- 更新 CLI、YAML、Gateway RouterManager/state/server 测试。
- 重写 `tests/integration/test_serial_multi_ai_router.py` 为广播聚合端到端测试。

用户工作区已删除的两份 2026-07-28 Router 汇总文档不恢复、不修改，也不纳入本功能提交。

## 14. 测试策略

所有生产行为按 red-green-refactor 实现。

### 14.1 配置与入口

- mode 必填且只接受两个枚举值。
- 空 Socket、空 description、重复 upstream、自递归、非法 timeout 和非法字符预算被拒绝。
- `setup_logging` 先于参数校验。
- 根 `Router` 为两种 mode 组装正确策略。
- CLI 和 YAML 正确解析二元 upstream，且不存在 default 字段。

### 14.2 广播聚合

- 使用 `anyio.Event` 证明全部 upstream 在任一分支释放前已经启动。
- 模拟乱序完成并断言反馈仍按配置顺序。
- 断言每个 upstream 收到相同公开请求，原请求未被修改。
- 断言 `model`、`routing` 不进入任何普通 AI 请求。
- 部分失败仍调用 Aggregator，并包含安全错误摘要。
- 全部失败不调用 Aggregator，只返回 Router 错误。
- Aggregator 失败不调用其他模型。
- 动态材料压缩确定且不删除候选标识、状态、tool ID 和 function name。

### 14.3 SSE、取消和工具

- 聚合结果的 content、reasoning、tool call 分片与合法 finish reason 被转发。
- Aggregator `finish_reason="error"` 和空结果转成 Router 错误。
- 客户端提前断开会关闭 Aggregator stream 并取消仍在运行的分支。
- 分支 tool calls 只出现在聚合材料中；最终只返回 Aggregator tool calls。
- aggregation 第二个 Session 工具回合重新广播全部 upstream。
- routing sticky 和现有 selector 安全映射保持回归通过。

### 14.4 Gateway 与端到端

- RouterManager 创建、删除、配置校验和 Socket 映射。
- REST 创建/列出 Router 的新 schema。
- 旧 state 忽略 default 字段并迁移 context 字段；新 state 不再持久化 default。
- Router-backed 标题生成解析到 `router_ai_id`。
- 三个真实 aiohttp mock upstream、一个 Aggregator、一个真实 Session 的完整链路只向用户返回最终聚合结果。

## 15. 验证命令

```text
uv run pytest -q tests/psi_agent/router tests/psi_agent/gateway/test_router_manager.py tests/psi_agent/test_cli.py tests/psi_agent/test_run.py tests/integration/test_serial_multi_ai_router.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

若修改 Gateway SPA，还需在 `src/psi_agent/gateway/spa/` 运行：

```text
npm test -- --run
npm run build
```

## 16. 验收标准

1. aggregation 请求确实并发到达全部配置 upstream。
2. 部分 upstream 失败时仍由专用 Aggregator 生成最终响应。
3. 全部 upstream 失败或 Aggregator 失败时只产生 Router 错误，不发生 fallback。
4. 聚合输入顺序稳定，不含真实候选 Socket 或 reasoning。
5. 分支工具调用不会绕过 Aggregator；Session 只执行最终聚合工具调用。
6. Channel/Session 协议不变，CLI、YAML 和 Gateway 均可启动两种 Router mode。
7. 旧 Gateway state 可恢复，新 state 和 UI 不再包含 default 模型。
8. Router 定向测试、完整测试、lint、format、type check 和受影响前端测试全部通过。
