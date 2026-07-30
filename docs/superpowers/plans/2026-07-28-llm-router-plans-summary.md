# LLM Router Plans 汇总（2026-07-24 至 2026-07-28）

本文合并整理 `docs/superpowers/plans/` 下 2026-07-24、2026-07-28 与 LLM Router 相关的实施计划，作为后续执行、回顾和维护 Router 工作的 plan 侧总览。

## 来源文档

- `docs/superpowers/plans/2026-07-24-dynamic-router.md`
- `docs/superpowers/plans/2026-07-28-router-routing-aggregation.md`
- `docs/superpowers/plans/2026-07-28-router-directory-reorganization.md`

## 计划演进

### 2026-07-24：动态 Router 实施计划

目标是实现最终的动态 Planner → selected upstream → Aggregator 流程，避免把每个请求广播给所有 upstream。

核心任务：

1. **动态计划协议**
   - `parse_plan()` 和 `Planner.plan()` 从固定三任务改为接受一个或多个任务。
   - 保留严格 JSON schema、root/task key 校验、配置 socket 白名单校验。
   - 更新 planning / repair prompts，让模型知道任务数量是动态的，并只能选择配置过的 socket。
   - 测试覆盖单任务、多任务、重复 socket、错误格式、一次 repair。

2. **定向子任务执行**
   - `Orchestrator.process()` 先调用 Planner。
   - 每个 planned task 构造独立请求，带原始 messages、完整 tools 和任务提示。
   - 只调用 Planner 选中的 socket，不再广播给所有 upstream。
   - 保留 Planner 输出顺序作为结果顺序。
   - 日志记录任务数量、子任务摘要和 socket。
   - 测试未选中 upstream 不被调用，选中任务可以并发执行。

3. **Router 模型聚合**
   - 将子任务名和子任务最终结果发送给 `router_socket`。
   - 聚合阶段保留 tools schema，使最终聚合模型可产生 tool calls。
   - 聚合 tool calls 按原始 ID 去重。
   - 聚合 prompt 必须要求只返回终端用户答案，不输出 routing JSON、内部规划或思维。
   - 测试纯文本、文本/tool-call 混合、重复 ID、异常聚合响应。

4. **Session 回合与 fallback**
   - 验证 Session 执行聚合阶段 tool calls，并使用同一 `routing.session_id` 发起下一轮请求。
   - 验证部分 upstream 失败时保留成功结果。
   - 验证全部阶段失败时只调用一次 `default_socket`。
   - 增加覆盖 Planner、多个定向 upstream、Aggregator、Session 工具执行和二轮请求的集成测试。

5. **结构与文档**
   - 早期计划中保留 `server.py` / `__init__.py` 为公共边界。
   - 当时计划使用 `routing.py`、`aggregation.py`、`prompts.py` 作为 facade / prompt 文件。
   - 后续 2026-07-28 的目录重组计划进一步替代了这一结构。

### 2026-07-28：routing / aggregation 双模式实施计划

目标是在同一个 Router 服务边界下新增显式启动模式，支持 `routing` 或 `aggregation`。

核心任务：

1. **必填模式和 upstream 协议**
   - 在 `RouterConfig` 中增加必填 `mode`。
   - 新增 `RouterMode`，值只能是 `routing` 或 `aggregation`。
   - `upstream` 对外输入固定为 `list[tuple[str, str]]` 或等价 tuple 容器。
   - 每个 upstream 条目自身必须是二元 tuple。
   - 启动缺少 `mode` 时直接报错。

2. **HTTP 边界策略接口**
   - `server.py` 不直接写模式分支，而是通过统一 `RouterStrategy` 协议调用策略对象。
   - 策略协议包含：
     - `process(body=...)`
     - `discard(session_id)`
     - `clear()`
   - `serve_router()` 接收 `strategy`，HTTP/SSE/fallback/错误处理保持一份实现。

3. **routing 策略**
   - `RoutingOrchestrator` 调用 `router_socket` 选择一个 socket。
   - 路由模型必须输出严格 JSON：

     ```json
     {"socket": "<configured socket>"}
     ```

   - 输出非法、未知 socket、额外 key、非 JSON 等情况全部 fallback 到 `default_socket`。
   - 合法选择时，把完整上下文转发给选中 socket。

4. **aggregation 策略**
   - 将既有 Planner / 子任务 / Aggregator 流程命名为 `AggregationOrchestrator`。
   - 保留 `Orchestrator = AggregationOrchestrator` 兼容别名，后续目录重组可进一步清理。
   - 子任务结果结构包含：
     - `subtask`
     - `content`
     - `tool_calls`
   - 子模型 tool calls 只作为聚合材料，不直接返回给 Session。
   - 最终聚合模型返回的 tool calls 才返回给 Session。

5. **启动面、Gateway 和文档**
   - `Router` dataclass / CLI / YAML / Gateway / SPA 创建路径都必须要求 `mode`。
   - Gateway 创建、恢复、持久化 Router 时都要保存并传递 `mode`。
   - 测试覆盖 mode 必填、两种策略 wiring、Gateway router manager、集成路由流程。

### 2026-07-28：目录重组实施计划

目标是把 Router 目录整理成两个模式目录：`routing/` 与 `aggregation/`，各自保存自己的策略实现和提示词；公共传输、协议、服务入口留在根目录。

最终目标结构：

```text
src/psi_agent/router/
├── __init__.py
├── entry.py
├── server.py
├── client.py
├── protocol.py
├── routing/
│   ├── __init__.py
│   ├── orchestrator.py
│   └── prompts.py
└── aggregation/
    ├── __init__.py
    ├── orchestrator.py
    ├── planner.py
    └── prompts.py
```

核心任务：

1. **创建功能目录并移动实现**
   - `routing.py` 的真实实现移动到 `routing/orchestrator.py`。
   - `build_routing_messages()` 移动到 `routing/prompts.py`。
   - 聚合实现移动到 `aggregation/orchestrator.py`。
   - Planner 移动到 `aggregation/planner.py`。
   - planning / repair / branch / aggregation prompt builders 移动到 `aggregation/prompts.py`。

2. **重连导入和公共导出**
   - 内部导入改用 `psi_agent.router.routing` 和 `psi_agent.router.aggregation`。
   - 根 package 继续导出公共类型和入口：
     - `Router`
     - `RouterConfig`
     - `RouterMode`
     - `RouterClient`
     - `RoutingOrchestrator`
     - `AggregationOrchestrator`
     - `serve_router`
   - 实现细节测试改用新 package 路径。

3. **文档与验证**
   - 更新 `src/psi_agent/router/AGENTS.md` 的目录结构。
   - 根目录只保留公共接口文件，不再保留旧的根级兼容业务文件。
   - 验证命令：

     ```bash
     uv run ruff check .
     uv run ruff format --check .
     uv run ty check
     uv run pytest -v tests/psi_agent/router tests/psi_agent/gateway/test_router_manager.py tests/psi_agent/test_run.py tests/integration/test_serial_multi_ai_router.py
     ```

## 当前实现约定

- `routing` 与 `aggregation` 是启动时固定模式，不支持请求级切换。
- `mode` 不允许缺失，不允许隐式默认。
- `default_socket` 只用于 fallback。
- Router 不执行工具。
- 子模型 tool calls 不直接交给 Session。
- 只有最终模型的 `content` / `reasoning` / `tool_calls` 会作为 Router 响应返回。
- 根目录不再承载模式业务逻辑；模式逻辑必须进入对应子目录。

## 后续维护提醒

- 新增或修改 Router 模式时，优先新增策略对象，不要在 `server.py` 里堆分支。
- 修改提示词时，应放在对应模式目录的 `prompts.py`。
- 修改 Planner 行为时，应同步 `aggregation/planner.py` 和相关 tests。
- 修改配置字段时，必须同步 CLI、YAML、Gateway、SPA 和 tests。
- 任何 fallback、tool-call、SSE 行为变更都需要补集成测试。
