# Router 实现记录（合并版）

本文合并 2026-07-23 Router 的设计规范、实现计划及后续并发路由调整，作为唯一维护记录。

## 最终目标

Router 是 Session 与多个无状态 AI socket 之间的 OpenAI-compatible HTTP/SSE 编排层。模型名称、provider、API key 均由各个 `psi-agent ai` 后端配置；Router 只认识 socket 和 description。

最终采用动态“路由 → 子任务 → 聚合”流程：

```text
Session
  ↓
router_socket 上的路由模型
  ↓  {tasks: [{subtask, socket}, ...]}
按任务选择 upstream socket（可并发）
  ↓
router_socket 上的聚合模型
  ↓
Session（文本、reasoning、去重后的 tool_calls）
  ↓ 工具由 Session 执行
下一轮重新进入 Router
```

路由模型根据主任务与 upstream description 的适配度动态决定任务数量，不固定为三个；相同 socket 可以承接多个子任务。被选中的任务按 Planner 输出顺序聚合，执行请求可以并发。

## 公共配置

```python
Router(
    session_socket: str,
    router_socket: str,
    default_socket: str,
    upstream: list[tuple[str, str]],
)
```

- `session_socket`：Router 接收 Session 请求的地址。
- `router_socket`：同时承载路由模型和聚合模型。
- `default_socket`：任意编排失败时的一次性 fallback 后端，不能等于 `session_socket`。
- `upstream`：`(socket, description)` 列表，不包含模型字段。

## 路由协议

Planner 收到完整用户消息和 socket 能力目录，只能输出已配置 socket：

```json
{
  "tasks": [
    {"subtask": "查询相关数据", "socket": "http://127.0.0.1:7001"},
    {"subtask": "分析数据并给出结论", "socket": "http://127.0.0.1:7002"}
  ]
}
```

Parser 要求 root 只包含 `tasks`，每个 task 只包含 `subtask`、`socket`，任务至少一个，socket 必须精确匹配配置。非法结果允许一次 repair，repair 仍非法则 fallback。

## 子任务与工具

每个选中的子任务收到完整 `messages` 和完整 `tools`，但只处理 Planner 分配的子任务。Router 不执行 workspace tools；子模型产生的工具调用由 Router 聚合后交给 Session。Session 执行工具并把工具结果写入正式 history，再以稳定的 `routing.session_id` 发起下一轮。

工具调用按原始 `tool_call.id` 去重：相同 ID 只保留第一次完整定义，不同 ID 即使名称和参数相同也保留。Router 不把内部 `routing` 字段转发给普通 AI provider。

## 聚合

聚合模型通过 `router_socket` 调用，收到原始任务和各子任务最终结果。Prompt 要求只输出面向用户的最终答案，不输出 `thought`、`tasks`、`backend_socket`、路由过程或 Markdown JSON。聚合结果保持单 choice SSE；有工具调用时使用 `finish_reason="tool_calls"`，否则使用 `stop`。

## 错误、fallback 与取消

- 部分 upstream 失败：保留成功任务并记录 warning。
- Planner、所有子任务或聚合全部失败：清理临时状态，向 `default_socket` 转发当前请求一次。
- fallback 失败：prepare 前返回 HTTP 502，prepare 后返回 `finish_reason="error"` SSE chunk。
- 所有 task group、SSE generator、aiohttp runner 在取消时清理；跨 await 的 cleanup 使用 shielded cancel scope。

## 代码结构

```text
src/psi_agent/router/
├── __init__.py      # 对外导出 Router、serve_router
├── server.py        # aiohttp HTTP/SSE、fallback、生命周期
├── routing.py       # 分流 facade（Planner、Orchestrator、RouterClient、配置类型）
├── aggregation.py   # 聚合 facade
├── prompts.py       # 路由、repair、子任务、聚合 Prompt
├── entry.py         # CLI/dataclass 启动编排
├── client.py        # socket/SSE 传输实现（兼容模块）
├── planner.py       # 动态计划解析和 repair（兼容模块）
├── orchestrator.py  # 子任务执行、去重、聚合编排（兼容模块）
└── protocol.py      # 类型和配置定义（兼容模块）
```

`routing.py` 和 `aggregation.py` 是新的职责入口；旧模块暂时保留兼容导入，后续可继续合并实现，但不应在兼容模块中新增业务逻辑。

## 日志与测试

INFO 记录 Planner 选中的任务/socket、成功 upstream 数量和聚合结果；SSE 原始 chunk 使用 DEBUG。Loguru 使用 f-string，不使用 `%s` 占位符。

测试覆盖动态任务数量、socket 白名单、并发子任务、聚合顺序、tool-call ID 去重、Session 多轮工具、部分失败和 default fallback。完成验证包括 `ruff check`、格式检查、`ty check`、Router/Session 测试、构建和 CLI help。

## 历史变更

1. 初版：固定三个子任务、串行分支、共享 router/aggregation socket。
2. 中间版：改为所有 upstream 广播并发，再本地聚合。
3. 最终版：恢复 llm_router 风格的 Planner → socket 定向子任务 → router_socket 聚合，并允许 Planner 根据 description 动态决定任务数量。
