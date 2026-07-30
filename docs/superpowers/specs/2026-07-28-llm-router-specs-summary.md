# LLM Router Specs 汇总（2026-07-24 至 2026-07-28）

本文合并整理 `docs/superpowers/specs/` 下 2026-07-24、2026-07-28 与 LLM Router 相关的设计规格，作为 Router 行为、协议和边界的 spec 侧总览。

## 来源文档

- `docs/superpowers/specs/2026-07-24-dynamic-router-design.md`
- `docs/superpowers/specs/2026-07-28-router-routing-aggregation-design.md`

## 设计目标

Router 位于 Session 与多个 AI 后端之间，提供一个统一的 OpenAI-compatible Chat Completions / SSE 服务边界。

设计目标从 2026-07-24 到 2026-07-28 分两阶段演进：

1. **动态聚合**
   - 路由模型根据用户请求和 upstream description 动态生成子任务计划。
   - 每个子任务只发送给计划中指定的 socket。
   - 子任务结果由同一个 `router_socket` 上的模型聚合后返回 Session。

2. **显式双模式**
   - 保留同一个 Router 服务边界。
   - 增加互斥的启动模式：
     - `routing`
     - `aggregation`
   - 模式必须在启动时显式指定，服务生命周期内固定。

## 统一配置边界

Router 配置必须包含：

- `mode`
- `session_socket`
- `router_socket`
- `default_socket`
- `upstream: list[tuple[str, str]]`

配置语义：

- `session_socket`：Router 对 Session 暴露的监听地址。
- `router_socket`：执行 routing selector、Planner 或 Aggregator 的模型后端。
- `default_socket`：兜底模型后端，只在失败场景使用。
- `upstream`：候选模型后端目录，每项是 `(socket, description)`。
- `mode`：启动模式，只允许 `routing` 或 `aggregation`。

Router 不配置 provider、model、api key；这些属于各 AI 后端自己的配置。

## routing 模式规格

`routing` 模式用于单后端分流。

数据流：

```text
Session
  -> Router service
       -> router_socket: routing selector
       -> selected upstream socket: full request
  -> Session
```

行为规格：

1. Router 接收 Session 的完整请求。
2. Router 将完整 `messages` 和 upstream 描述目录交给 `router_socket`。
3. 路由模型必须返回严格 JSON：

   ```json
   {"socket": "<configured socket>"}
   ```

4. Router 校验返回值。
5. 合法时，把原始完整上下文转发给选中 upstream。
6. 非法时，使用 `default_socket` 执行当前完整上下文。

非法输出包括：

- 非 JSON。
- 非 object。
- 缺少 `socket`。
- 包含额外 key。
- `socket` 不是字符串。
- `socket` 不在配置的 upstream 中。

## aggregation 模式规格

`aggregation` 模式用于多后端子任务聚合。

数据流：

```text
Session
  -> Router service
       -> router_socket: Planner
       -> selected upstream sockets: subtasks
       -> router_socket: Aggregator
  -> Session
```

行为规格：

1. Planner 基于完整上下文和 upstream 描述生成子任务计划。
2. 子任务数量不固定，但至少一个。
3. Planner 输出必须是严格 JSON：

   ```json
   {
     "tasks": [
       {"task_type": "...", "subtask": "...", "socket": "..."}
     ]
   }
   ```

4. 每个 socket 必须精确匹配配置中的 upstream socket。
5. Router 只调用计划中选中的 socket。
6. 子任务请求包含：
   - 原始完整 messages。
   - 完整 tools schema。
   - 子任务说明。
   - 必要时包含已完成子任务的最终答案。
7. 不同 socket 的子任务可以并发。
8. 同一 socket 上的多个子任务需要串行。
9. 子任务结果按 Planner 输出顺序进入 Aggregator。
10. Aggregator 生成面向最终用户的答案。

Aggregator 输出要求：

- 不输出 routing JSON。
- 不输出 backend socket。
- 不输出内部计划。
- 不输出 Markdown fence 包裹的结构化控制信息。
- 只输出给最终用户看的答案，或最终需要 Session 执行的 tool calls。

## 工具调用规格

Router 不执行工具。

具体规则：

- Router 不加载 workspace tools。
- Router 不调用 ToolRegistry。
- Router 不写 conversation history。
- 子任务可以看到完整 tools schema。
- aggregation 模式下，子任务返回的 `tool_calls` 只是聚合材料。
- 子任务 tool calls 不直接返回给 Session。
- 最终 Aggregator 返回的 `tool_calls` 才能进入 Router 响应。
- Session 是唯一工具执行方。

Session 工具回合：

1. Session 收到 Router 最终响应中的 tool calls。
2. Session 执行工具。
3. Session 写入工具结果。
4. Session 使用稳定的 `routing.session_id` 发起下一轮 Router 请求。

## fallback 规格

`default_socket` 是单次兜底后端。

### routing fallback

触发条件：

- routing selector 输出非法。
- routing selector 选中未知 socket。
- routing selector 调用失败。
- 选中 upstream 出现不可恢复错误。

fallback 行为：

- Router 移除内部 `routing` metadata 和 `model` 字段。
- Router 把完整公开请求转发给 `default_socket`。
- fallback 只执行一次。

### aggregation fallback

触发条件：

- Planner 输出为空。
- Planner 输出格式错误。
- Planner 输出包含未配置 socket。
- 所有子任务全部失败。
- Aggregator 失败。
- Aggregator 返回不可用结果。

部分子任务失败时：

- 不立即 fallback。
- 成功结果继续进入聚合。
- 失败事实进入聚合上下文，供最终模型说明或补救。

## HTTP / SSE 错误规格

Router 遵守 psi-agent 统一错误边界：

1. `response.prepare()` 前失败：

   ```json
   {"error": {"message": "...", "type": "...", "param": null, "code": 400}}
   ```

2. SSE 已经开始后失败：

   ```json
   {
     "id": "error",
     "choices": [
       {
         "index": 0,
         "delta": {"content": "[Router Error]: ..."},
         "finish_reason": "error"
       }
     ]
   }
   ```

所有层继续保持单 choice SSE 约定。

## 取消安全与日志规格

Router 必须保持：

- 所有 IO 使用 `anyio`。
- 不使用原生 `asyncio` API。
- 每个 SSE chunk 边界使用 DEBUG 日志。
- 启动、关闭、请求完成使用 INFO 日志。
- 可恢复异常使用 WARNING。
- 不可恢复错误使用 ERROR。
- async generator 提前退出时必须关闭。
- 清理逻辑放在 `finally`、`except` 或 async context manager 中。
- 跨 `await` 的清理使用 shielded cancel scope。

## 兼容性规格

这是一次显式配置变更：

- 不再允许缺省模式。
- 不再允许“缺省即 aggregation”。
- 不再允许“缺省即 routing”。
- 所有启动路径必须要求 `mode`。
- `mode` 只出现在启动配置中，不出现在 Chat Completions 请求体中。

这样做是为了让 routing 与 aggregation 两种明显不同的行为在配置、日志、测试和故障排查中都保持清晰。

## 最终目录规格

Router 的最终目录边界为：

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

根目录只保留公共接口、传输、协议和启动入口。模式相关业务逻辑必须放入对应模式目录。
