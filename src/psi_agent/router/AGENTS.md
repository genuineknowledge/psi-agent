# Router 层设计文档

## 概述

Router 位于 Session 与多个 AI 后端之间，负责把一次 Session 请求交给路由模型判断，再将子任务发送到匹配的 upstream，最后由同一个 `router_socket` 上的模型聚合结果。Router 不加载 workspace tools，也不维护正式会话历史；工具始终由 Session 执行。

## 目录结构

```text
router/
├── __init__.py              # Router 对外统一导出
├── entry.py                 # CLI/dataclass 启动入口，按 mode 选择策略
├── server.py                # HTTP/SSE 服务、fallback、生命周期
├── client.py                # socket/SSE 传输实现
├── protocol.py              # 类型和配置定义
├── routing/
│   ├── __init__.py          # 分流模式对外导出
│   ├── orchestrator.py      # 分流策略：选择一个 upstream 并转发完整上下文
│   └── prompts.py           # 分流模式提示词
└── aggregation/
    ├── __init__.py          # 聚合模式对外导出
    ├── orchestrator.py      # 聚合策略：规划、分发子任务、汇总结果
    ├── planner.py           # 聚合模式的任务规划和计划校验
    └── prompts.py           # 聚合模式提示词
```

业务逻辑必须放在 `routing/` 或 `aggregation/` 内部；根目录只保留传输、协议和启动入口，不得新增模式相关业务逻辑。

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

## 模式切换契约

Router 启动时必须显式指定模式，不存在隐式默认模式。

- `routing` 表示分流模式：路由模型从已配置的 upstream socket 中选择一个，并把完整请求转发给该 socket。
- `aggregation` 表示聚合模式：先把任务分发给已配置的 upstream，再由聚合模型综合子结果生成最终回复。
- CLI、YAML、Gateway 和 SPA 的 Router 创建路径都必须要求传入 `mode`，并且原样透传，不得丢失或自行补默认值。
