# Unified Router Routing Synchronization Plan

> **Design spec:** `docs/superpowers/specs/2026-08-04-router-routing-design.md`
>
> **Related aggregation spec:**
> `docs/superpowers/specs/2026-08-04-broadcast-aggregation-router-design.md`
>
> **Plan status:** 已按当前实现完成。本文档记录 Routing 从独立单模式实现同步到统一 Router
> 架构时的实际任务边界，不要求或依赖任何特定插件。

## Objective

在保留单目标选择和 tool-call sticky 行为的前提下，让 Routing 与新增 Aggregation 共享传输、
目标类型、请求复制和统一入口，并同步 CLI、YAML、Gateway、OpenAPI、SPA、文档和测试契约。

最终必须同时满足：

- `Router(mode="routing")` 是 CLI/Gateway 的统一入口；
- `RoutingRouter` 仍可单独嵌入；
- `RoutingTarget` 是共享 `RouterTarget` 的兼容别名；
- Routing 的 Selector 行为和 sticky 生命周期不退化；
- 不恢复任何 Planner 或旧 Router API；
- Routing 允许 Selector AI 同时作为候选，不能误套 Aggregation 的专用服务限制。

## Constraints

- 异步操作使用 AnyIO，不新增原生 `asyncio` 调用。
- 每个有效 SSE event 恰好一个 choice；0 choice 静默跳过，多 choice 报错。
- 所有包装或消费 async generator 的边界使用 `aclosing()`。
- `run()` 的第一行可执行语句必须是 `setup_logging(verbose=self.verbose)`。
- 请求中除 `model`、`routing` 外的公开和未知参数继续透传。
- Socket 只来自启动配置，不接受 Selector 或 Channel 动态指定。
- 不新增 `noqa`、`per-file-ignores` 或旧 API 兼容壳。
- 只在 Gateway state 加载时单向读取旧字段，正常保存只写新字段。

## Current File Map

```text
src/psi_agent/router/
├── __init__.py              # 统一公开导出
├── entry.py                 # Router(mode=routing|aggregation)
├── models.py                # RouterMode / RouterTarget / CompletionResult
├── request.py               # copy_public_request_body
├── client.py                # 共享 Socket-aware HTTP/SSE client
├── server.py                # 共享 OpenAI-compatible server
├── routing/
│   ├── entry.py             # 独立 RoutingRouter
│   ├── models.py            # RoutingConfig / RoutingTarget alias
│   ├── prompts.py           # Selector prompt
│   ├── selector.py          # 严格 ID 选择
│   └── strategy.py          # 单目标代理与 sticky
└── aggregation/             # 独立广播聚合策略
```

## Task 1: Promote Cross-Mode Contracts

**Files:**

- Modify: `src/psi_agent/router/models.py`
- Modify: `src/psi_agent/router/routing/models.py`
- Modify: `src/psi_agent/router/__init__.py`
- Test: `tests/psi_agent/router/test_models.py`
- Test: `tests/psi_agent/router/test_entry.py`

- [x] 新增显式 `RouterMode.ROUTING` 和 `RouterMode.AGGREGATION`。
- [x] 把候选公共字段提升为 `RouterTarget(candidate_id, socket, description)`。
- [x] 保留 `RoutingTarget = RouterTarget`，不维护两套 target 类型。
- [x] 保持候选 ID 格式、字符串规范化和非空校验。
- [x] 保持 `CompletionResult` 为共享 buffered completion 结果。
- [x] 更新 package exports，导出当前两种模式，同时拒绝旧 process API 回流。

**Gate:**

```text
uv run pytest -q tests/psi_agent/router/test_models.py tests/psi_agent/router/test_entry.py
```

## Task 2: Share Public Request Projection

**Files:**

- Create: `src/psi_agent/router/request.py`
- Modify: `src/psi_agent/router/routing/strategy.py`
- Test: `tests/psi_agent/router/test_request.py`
- Test: `tests/psi_agent/router/test_routing.py`

- [x] 实现 `copy_public_request_body()`。
- [x] 深拷贝所有输入字段，保证调用方 body 不被修改。
- [x] 删除 `model` 和 Router 内部 `routing` 元数据。
- [x] 强制 `stream=True`。
- [x] 保留 messages、tools、tool_choice、采样参数和未知公开字段。
- [x] Routing 和 Aggregation 复用同一函数，不在各策略中复制投影逻辑。

**Gate:**

```text
uv run pytest -q tests/psi_agent/router/test_request.py tests/psi_agent/router/test_routing.py
```

## Task 3: Preserve Selector and Sticky Semantics

**Files:**

- Modify: `src/psi_agent/router/routing/models.py`
- Modify: `src/psi_agent/router/routing/selector.py`
- Modify: `src/psi_agent/router/routing/strategy.py`
- Test: `tests/psi_agent/router/test_routing.py`
- Test: `tests/psi_agent/router/test_client.py`

- [x] `RoutingConfig` 接受共享 `RouterTarget`，并规范化为 tuple。
- [x] 保持 session/selector/target Socket 的递归和唯一性检查。
- [x] 保持有限正 timeout 和正整数字符预算校验，不接受 `bool`。
- [x] Selector 请求只包含候选 ID/描述、压缩对话和工具摘要。
- [x] Selector 决策严格限制为 `{"candidate_id":"..."}`。
- [x] 继续通过本地 ID 映射取得目标 Socket。
- [x] 普通轮次重新选择，末条 `role="tool"` 且 sticky 存在时复用目标。
- [x] 只在目标终态为 `tool_calls` 时保留 sticky。
- [x] 异常、断连、未完整消费、非 tool 终态、显式 discard 和 shutdown 都清理 sticky。
- [x] 保持 `compaction_needed` 不覆盖真实终态。
- [x] Routing 不禁止 Selector AI 同时作为 upstream。

**Gate:**

```text
uv run pytest -q tests/psi_agent/router/test_routing.py tests/psi_agent/router/test_client.py
```

## Task 4: Add the Unified Router Facade

**Files:**

- Create: `src/psi_agent/router/entry.py`
- Modify: `src/psi_agent/router/__init__.py`
- Keep: `src/psi_agent/router/routing/entry.py`
- Test: `tests/psi_agent/router/test_entry.py`

- [x] 定义统一 `Router` dataclass：

  ```python
  Router(
      session_socket,
      router_socket,
      mode,
      upstream,
      router_timeout=30.0,
      target_timeout=None,
      max_context_chars=12_000,
      verbose=False,
  )
  ```

- [x] 要求显式 mode，不提供缺省 Routing。
- [x] 按 upstream 配置顺序生成 `candidate-1...N`。
- [x] Routing 模式映射：

  ```text
  router_socket      -> selector_socket
  router_timeout     -> selector_timeout
  target_timeout     -> target_timeout
  max_context_chars  -> max_selection_chars
  ```

- [x] 为一次 run 创建一个共享 `RouterHttpClient`，供 Selector 和目标流使用。
- [x] `Router.run()` 在任何参数校验前配置 logging。
- [x] 保留 `RoutingRouter` 的专用字段和嵌入入口。
- [x] 不增加 `RouterClient`、`UpstreamResult`、`stream_raw`、`Orchestrator` 等旧别名。

**Gate:**

```text
uv run pytest -q tests/psi_agent/router/test_entry.py
```

## Task 5: Keep the Shared HTTP/SSE Boundary Mode-Neutral

**Files:**

- Modify: `src/psi_agent/router/client.py`
- Modify: `src/psi_agent/router/server.py`
- Test: `tests/psi_agent/router/test_client.py`
- Test: `tests/psi_agent/router/test_server.py`

- [x] server 只依赖 `RouterStrategy` protocol，不按 mode 分支。
- [x] prepare 前校验 JSON、messages、tools、stream 和 routing 元数据。
- [x] prepare 后错误转成单 choice `finish_reason="error"` SSE。
- [x] client 跳过 0 choice，拒绝多 choice 和非法 payload。
- [x] client 要求真实 completion finish reason，辅助 compaction 信号不覆盖终态。
- [x] client 和 server 对 generator 使用 `aclosing()`。
- [x] startup failure、取消和 shutdown 使用 shielded cleanup。
- [x] Routing 的客户端断开仍触发 `discard(session_id)`。

**Gate:**

```text
uv run pytest -q tests/psi_agent/router/test_client.py tests/psi_agent/router/test_server.py
```

## Task 6: Wire CLI and YAML to the Unified Entry

**Files:**

- Modify: `src/psi_agent/cli.py`
- Modify: `src/psi_agent/_run.py`
- Test: `tests/psi_agent/test_cli.py`
- Test: `tests/psi_agent/test_run.py`

- [x] CLI top-level command使用 `Router`，不再依赖 Routing-only 入口。
- [x] `--mode routing` 必填并映射为 `RouterMode.ROUTING`。
- [x] `--router-socket` 在 Routing 模式表示 Selector AI。
- [x] `--upstream` 保持二元 `(socket, description)` 配置。
- [x] 暴露 `router_timeout`、`target_timeout` 和 `max_context_chars`。
- [x] YAML `type: router` 把二元 list 规范化为 tuple。
- [x] 删除对 `default_socket` 和缺省 mode 的读取。

**Gate:**

```text
uv run pytest -q tests/psi_agent/test_cli.py tests/psi_agent/test_run.py
```

## Task 7: Synchronize Gateway, State, OpenAPI, and SPA

**Files:**

- Modify: `src/psi_agent/gateway/_router_manager.py`
- Modify: `src/psi_agent/gateway/_state.py`
- Modify: `src/psi_agent/gateway/__init__.py`
- Modify: `src/psi_agent/gateway/server.py`
- Modify: `src/psi_agent/gateway/_openapi.py`
- Modify: `src/psi_agent/gateway/spa/src/routerConfig.js`
- Modify: `src/psi_agent/gateway/spa/src/stores/router.js`
- Modify: `src/psi_agent/gateway/spa/src/components/RouterDialog.vue`
- Modify: `src/psi_agent/gateway/spa/src/components/HubModelsPanel.vue`
- Test: Gateway Router/state/OpenAPI/server tests and SPA tests

- [x] Gateway 使用统一 `Router` 启动 Routing 或 Aggregation。
- [x] 用户配置继续保存 AI ID，启动前映射为私有 Socket。
- [x] RouterInfo 只保留：
  `id/name/socket/mode/router_ai_id/upstreams/router_timeout/target_timeout/max_context_chars`。
- [x] Routing 允许 `router_ai_id` 同时作为 upstream；Aggregation 单独禁止。
- [x] REST create/list/delete 与 OpenAPI 使用当前字段。
- [x] Router-backed 标题/摘要模型解析使用 `router_ai_id`。
- [x] state load 忽略 `default_ai_id`。
- [x] state load 优先 `max_context_chars`，否则迁移 `max_context_length`。
- [x] state save 不写 `default_ai_id` 或 `max_context_length`。
- [x] SPA 根据 mode 显示 Selector/Aggregator，并移除默认模型字段。
- [x] SPA 单独校验 Router timeout、target timeout 和字符预算。

**Gate:**

```text
uv run pytest -q \
  tests/psi_agent/gateway/test_router_manager.py \
  tests/psi_agent/gateway/test_state.py \
  tests/psi_agent/gateway/test_openapi.py \
  tests/psi_agent/gateway/test_server.py

cd src/psi_agent/gateway/spa
npm test -- --run
npm run build
```

## Task 8: Update Routing Documentation and Regression Coverage

**Files:**

- Modify: `docs/superpowers/specs/2026-08-04-router-routing-design.md`
- Modify: `docs/superpowers/plans/2026-08-04-router-routing.md`
- Modify: `src/psi_agent/router/README.md`
- Create/Modify: mirrored Router and Gateway tests

- [x] 把 Routing 文档从“唯一模式”改为“统一 Router 下的单目标策略”。
- [x] 记录共享 `RouterMode`、`RouterTarget` 和请求复制边界。
- [x] 记录统一字段到 RoutingConfig 的映射。
- [x] 记录 CLI/YAML/Gateway/SPA 的当前接口。
- [x] 明确 Routing 与 Aggregation 的不同 Socket 复用规则。
- [x] 删除已不存在的测试路径和旧 API 引用。
- [x] 保持 Selector、单目标代理和 tool sticky 的验收标准。

## Final Verification

- [x] Router/Gateway/CLI/真实链路影响面测试通过。
- [x] `uv run ruff check src tests` 通过。
- [x] `uv run ty check .` 通过。
- [x] SPA 63 项测试通过，生产构建成功。
- [x] `git diff --check` 通过。
- [x] 未新增 Superpowers 插件依赖、诊断抑制或旧 Router API。

推荐的 Routing 定向复核命令：

```text
uv run pytest -q \
  tests/psi_agent/router/test_models.py \
  tests/psi_agent/router/test_request.py \
  tests/psi_agent/router/test_client.py \
  tests/psi_agent/router/test_routing.py \
  tests/psi_agent/router/test_entry.py \
  tests/psi_agent/router/test_server.py \
  tests/psi_agent/test_cli.py \
  tests/psi_agent/test_run.py

uv run ruff check src/psi_agent/router tests/psi_agent/router
uv run ty check src/psi_agent/router tests/psi_agent/router
```

## Completion Criteria

1. Routing 保持一次选择、一次目标调用和 tool-run sticky。
2. 统一 Router/CLI/Gateway 显式选择 `mode="routing"`。
3. RoutingTarget 兼容接口和 RoutingRouter 嵌入入口继续可用。
4. Routing 与 Aggregation 共享传输和公开请求契约，但不共享模式状态。
5. Gateway 与 SPA 不再出现 default AI 或旧 context-length 字段。
6. 文档、测试名称和实际文件布局一致。
