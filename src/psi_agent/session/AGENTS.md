# Session 层设计文档

## 概述

Session 层是 psi-agent 的核心——负责 workspace 解析、agent loop、tool 执行、schedule 调度以及面向 Channel 的 HTTP/SSE 服务。

## Workspace 启动流程

`Session.run()` 的启动顺序（由 `SessionAgent.create()` 完成 workspace 加载）：

```
1. setup_logging(verbose)
2. 解析 workspace 路径（anyio.Path.resolve()）
3. SessionAgent.create() → 加载 tools、schedules、system_prompt_builder
4. 创建 anyio.Lock()
5. 启动 anyio.task_group：
   ├── serve_session(handler=agent.handle_chat_completions, ...)
   └── 每个 schedule 一个 run_one_schedule() task
```

**关键点**：
- Workspace 加载全部由 `SessionAgent.create()` 完成——`__init__.py` 只做入口编排
- Tool 加载为单遍 `importlib`：同时产出元数据（ToolFunction）和实际 callable
- System prompt 在首次 `run()` 调用时惰性构建（通过 `system_prompt_builder`）

## Agent Loop 逻辑

1. 收到 channel 请求 → `handle_chat_completions`（SessionAgent 方法）
2. 提取 messages[-1] 作为 user_message，其余字段透传为 extra_params
3. 惰性构建 system prompt（首次 run() 时，如果 history 尚无 system 消息）
4. 检查暂存的 schedule 响应 → 有则先流式返回
5. 获取 `anyio.Lock`（忙则 FIFO 排队等待）
6. User message 追加到 history
7. 发送 `history + tools + extra_params` 到 AI socket（streaming）
8. 解析 SSE 流（每 chunk 恰好 1 个 choice，多 choice 报错，0 choice 心跳跳过）：
   - content → yield 给 channel
   - reasoning_content → yield 给 channel
   - tool_calls → 累积（按 index 拼接 partial JSON）
   - finish_reason="tool_calls" → 执行 tool → 结果追加到 history → 回到步骤 6
   - finish_reason="stop" → 最终 content 追加到 history → 释放锁
   - finish_reason="error" → 不写入 history，直接返回
8. 最多 `max_tool_rounds` 轮 tool call

**注意**：
- Channel 不发送 history。每次请求只带最新一条 user message，Session 自己维护完整 history。
- `response.prepare()` 在 lock 内执行——客户端在 lock 释放前不会看到 HTTP 200。
- `handle_chat_completions` 是 SessionAgent 的成员方法，以 bound method 传给 `serve_session`。
- Channel 请求中除 `messages` 外的不认识参数全部透传到 AI 层（`extra_params`）。
- AI 返回多 choice 时报错（`finish_reason="error"`），0 choice 作为心跳跳过。
- AI 返回非 200 或 `finish_reason="error"` 时，错误信息不写入 conversation history。

## 其他约定

- AI 连接超时：`ClientTimeout(total=300, sock_connect=30, sock_read=60)`
- 流式 `delta` 字段可能为 `null`（非缺失 key），agent 用 `isinstance(delta_data, dict)` 校验
- Tool 模块在 `sys.modules` 中以 `psi_tool_` 前缀注册，避免与 stdlib 同名冲突
- Schedule 加载时捕获坏 cron 表达式（不会导致整个 session 启动崩溃）

## SessionAgent 支持多种传输

`SessionAgent.ai_socket` 支持三种传输协议：
- `http(s)://host:port` → TCP（`TCPConnector`）
- `\\\\.\\pipe\\name` → Windows Named Pipe（`NamedPipeConnector`）
- 裸文件系统路径 → Unix domain socket（`UnixConnector`）

## Tool 加载约定

- `workspace/tools/*.py` 中的每个 `.py` 文件（不含 `_` 开头）
- 文件中所有非 `_` 开头的 `async def` 函数都会被加载为 tool
- 参数类型必须为 `str`、`int`、`float`、`bool`、`list[X]` 或 `X | None`（`Optional[X]`）
- `*args`、`**kwargs` 和多类型 Union（`int | str`）不支持，抛 `TypeError`
- `get_type_hints()` 解析失败时直接抛异常——tool 被跳过并打印 error
- 只支持 Google-style docstring（`Args:` 段落，`Returns:` 和 `Yields:` 作为描述结束标记）
- 用 `inspect.signature()` 提取参数（类型注解 → JSON Schema 类型）
- 用 `inspect.getdoc()` 提取描述（支持 Google-style 的 `Args:` 格式）
- 同名 tool 会被跳过并打印 warning（按文件加载顺序）

## Tool 调用细节

**参数类型解析**：
由于项目全量使用 `from __future__ import annotations`，函数注解以字符串形式存储。因此 `ToolFunction.from_callable()` 必须用 `typing.get_type_hints()` 解析，**不能**直接读 `param.annotation`。

**流式 Tool Call 累积**：
AI 的 tool_calls 通过 SSE 流式传输——多个 chunk 中的 `delta.tool_calls` 逐步补充同一 index 的参数。Agent 用 `accumulated_tool_calls: dict[int, dict]` 按 index 累积：
- `id`：取第一次非空值
- `function.name`：取第一次非空值
- `function.arguments`：**拼接**所有 partial JSON 片段

收到 `finish_reason="tool_calls"` 后，按 index 排序生成完整 tool_calls 列表，逐一执行。

**Tool 执行容错**：
- `arguments` 可能不是合法 JSON → `json.loads` 包在 try/except 中，失败时 fallback 为 `{}`
- Tool 函数可能抛异常 → 以错误文本作为 tool result 返回，不中断 agent loop
- Tool 返回非字符串（int, None） → 通过 `str()` 强转

## Schedule 机制完整流程

```
每个 schedule 一个 run_one_schedule() coroutine：
  while True:
    croniter.get_next()         ← 计算下次触发时间
    await anyio.sleep(触发时间 - now) ← 睡到触发
    async with lock:            ← 等当前请求完成
      调用 agent.run(msg)       ← AI 处理
      流式结果追加到 pending_chunks
      agent.set_pending_schedule_chunks(chunks)
      ↓
    下次 channel 请求到达时：
      SessionAgent.run() 开头先 yield 所有 _pending_schedule_chunks
      然后正常处理当前 channel 消息
```

关键点：
- Schedule 是纯配置数据类（`name, cron, task_content`），cron 状态由 `run_one_schedule` 维护
- Schedule 响应的 content 和 reasoning_content 各自存在于各自的消息周期，不会交错
- 多个 schedule 可以并发 sleep，但通过 lock 串行触发
- cron 表达式在加载时验证，非法表达式会导致该 schedule 被跳过
