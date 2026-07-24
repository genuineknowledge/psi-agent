# Dynamic Router Design

## 目标

Router 按 `llm_router` 的职责划分实现动态分流：路由模型根据主任务和 upstream description 生成子任务及目标 socket；子任务发送到对应 AI；同一个 `router_socket` 上的模型聚合子任务结果后返回 Session。

## 数据流

```text
Session
  -> router_socket: Planner
  -> selected upstream sockets: subtasks
  -> router_socket: Aggregator
  -> Session
```

Planner 输出任务数量不固定，至少一个任务；同一 socket 可以被多个任务选择。只允许选择 `upstream` 中配置的完整 socket 地址：

```json
{
  "tasks": [
    {"subtask": "查询数据", "socket": "http://127.0.0.1:7001"},
    {"subtask": "分析数据", "socket": "http://127.0.0.1:7002"}
  ]
}
```

任务请求可以并发执行，但每个任务只发送到 Planner 指定的 socket，不再广播给全部 upstream。结果按照 Planner 输出顺序送入聚合模型。

## 工具与 Session

Router 不执行 workspace tools。每个子任务收到完整 tools；聚合后的 tool calls 交给 Session 执行。Session 写入工具结果后，以稳定的 `routing.session_id` 发起下一轮 Router 请求。

工具调用按原始 `tool_call.id` 去重，相同 ID 仅保留第一次完整定义。Router 不把内部 routing 元数据转发给普通 AI provider。

## 错误处理

Planner、子任务或 Aggregator 全部失败时，清理临时状态并向 `default_socket` fallback 一次。部分子任务失败时保留成功结果。Router 保持单 choice SSE、HTTP/SSE 分层错误和取消安全清理约定。

## 配置边界

Router 只配置 `session_socket`、`router_socket`、`default_socket` 和 `upstream: list[tuple[str, str]]`；模型、provider、API key 由各 AI 后端自行配置。
