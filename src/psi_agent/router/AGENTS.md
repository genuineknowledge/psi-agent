# Router 层设计文档

## 概述

Router 位于 Session 与多个 AI 后端之间，负责把一次 Session 请求交给路由模型判断，再将子任务发送到匹配的 upstream，最后由同一个 `router_socket` 上的模型聚合结果。Router 不加载 workspace tools，也不维护正式会话历史；工具始终由 Session 执行。

## 目录结构

Router 代码按职责分为五类：

| 文件/职责 | 说明 |
|---|---|
| `__init__.py` | 对外暴露 `Router` 和 `serve_router`。不放业务逻辑。 |
| `server.py` | aiohttp HTTP/SSE 入口、fallback、生命周期和响应编码。 |
| `entry.py` | CLI/dataclass 启动编排；负责构造配置、客户端、分流器并启动 server。 |
| `planner.py`、`orchestrator.py`、`protocol.py`、`client.py` | 分流脚本及其内部协议/传输适配：Planner 根据 description 生成 `{subtask, socket}`，Orchestrator 调用选中的 upstream。 |
| `prompts.py` | 路由、修复、子任务和聚合 prompt 的纯函数构造。 |
| `aggregation.py`（如拆分时） | 聚合脚本：将子任务结果交给 `router_socket` 聚合，并处理 tool-call ID 去重。 |

后续重构应将 `client.py`、`protocol.py` 等纯支撑代码合并到分流脚本或聚合脚本中，保持 Router 顶层只剩 `server.py`、分流脚本、聚合脚本和 `prompts.py`。迁移期间允许保留兼容导入模块，但不得在其中增加新的业务逻辑。

## 数据流

```text
Session -> router_socket (Planner)
        -> upstream[socket] (selected subtasks, concurrent)
        -> router_socket (Aggregator)
        -> Session (content/reasoning/tool_calls)
```

Planner 接收完整的 upstream `(socket, description)` 目录，只能输出已配置 socket。任务数量由主任务与 description 的适配度决定，不固定为三个。被选中的子任务可以并发执行；结果按 Planner 输出顺序聚合。相同原始 `tool_call.id` 只保留第一次完整定义。

## Session 与工具

Router 不执行工具。聚合结果包含 `tool_calls` 时，Session 执行唯一的 ToolRegistry，并将工具结果写入 history 后重新请求 Router，开始下一轮“分流 → 子任务 → 聚合”。内部 `routing.session_id` 仅用于关联请求，普通 AI provider 转发前必须移除。

## 错误与取消

- Planner、upstream 或 Aggregator 全部失败时，server 只调用一次 `default_socket`。
- 部分 upstream 失败时保留成功结果，并在聚合前记录 warning。
- 所有 task group、SSE generator、aiohttp runner 在取消时必须清理；跨 await 的清理使用 shielded cancel scope。
- `setup_logging(verbose=...)` 必须是 `Router.run()` 第一条可执行语句。

## 日志

INFO 级别必须记录实际 Planner 计划、选中的 socket、成功 upstream 数量和聚合结果；逐 chunk 的原始 SSE 内容使用 DEBUG。不要使用标准 logging 的 `%s` 占位符，loguru 日志使用 f-string。

## 测试

测试目录镜像 `tests/psi_agent/router/`。必须覆盖：动态任务数量、socket 白名单、并发子任务、聚合顺序、tool-call ID 去重、Session 多轮工具调用、部分失败和 default fallback。
