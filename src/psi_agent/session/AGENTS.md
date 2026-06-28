# Session 层设计文档

## 概述

Session 层是 psi-agent 的核心——负责 workspace 解析、agent loop、tool 执行、schedule 调度以及面向 Channel 的 HTTP/SSE 服务。

## Workspace 启动流程

`Session.run()` 的启动顺序（由 `SessionAgent.create()` 完成 workspace 加载）：

```
1. setup_logging(verbose)
2. 解析 workspace 路径（空字符串时用 Path.cwd()，否则 anyio.Path.resolve()）
3. SessionAgent.create() → 生成 agent_uuid、创建 AiClient/ChannelAdapter/anyio.Lock、加载 tools/schedules/system 模块
4. 启动 anyio.task_group：
   ├── serve_session(agent=agent)  ← 从 agent 读取 channel_socket + handle_request
   └── 每个 schedule 一个 run_one_schedule(schedule, agent) task

**关键点**：
- `SessionAgent` 自包含：持有 `_agent_uuid`、`_ai_client`、`_channel_adapter`、`_channel_socket`、`_lock`
- 所有手动 `importlib` 使用 `原名_agent_uuid_文件hash[:12]` 作为 module name，确保同进程多 agent 隔离
- `SessionAgent.create()` 完成所有初始化——`__init__.py` 只做入口编排
- Tool 加载为单遍 `importlib`：同时产出元数据（ToolFunction）和实际 callable
- System prompt 在首次 `run()` 调用时惰性构建（通过 `system_prompt_builder`）
- 后续请求可调用 `system_prompt_rebuild_checker()`（如果定义），返回 True 则重建 system prompt

## Agent Loop 逻辑

1. 收到 channel 请求 → `ChannelAdapter.handle()` 解析请求，提取 user_message + extra_params
2. 惰性构建或重建 system prompt（首次 run 或 rebuild checker 返回 True 时）
3. 检查暂存的 schedule 响应 → 有则先流式返回（AgentChunk）
4. 获取 `anyio.Lock`（忙则 FIFO 排队等待）
5. User message 追加到 history
6. 通过 `AiClient.stream()` 发送 `history + tools + extra_params` 到 AI backend（streaming）
7. 消费 `AiDelta` 流（AiClient 已做好 SSE 解析、错误检测）：
   - content → `yield AgentChunk(content=...)` 给 ChannelAdapter
   - reasoning → `yield AgentChunk(reasoning=...)` 给 ChannelAdapter
   - tool_calls → 累积（按 index 拼接 partial JSON）
   - `finish_reason="tool_calls"` → 执行 tool → 结果追加到 history → 回到步骤 6
   - finish_reason="stop" → 最终 content 追加到 history → 释放锁
   - finish_reason="error" → `raise AgentError(message)`，不写入 history
8. 最多 `max_tool_rounds` 轮 tool call

**注意**：
- Channel 不发送 history。每次请求只带最新一条 user message，Session 自己维护完整 history。
- `response.prepare()` 在 lock 内执行——客户端在 lock 释放前不会看到 HTTP 200。
- `SessionAgent.handle_request()` 编排完整请求生命周期：parse → lock+prepare → run → write。
- `ChannelAdapter` 是纯无状态工具——不持有 agent/lock 引用。
- Channel 请求中除 `messages` 外的不认识参数全部透传到 AI 层（`extra_params`）。
- AI 返回多 choice 时报错（`finish_reason="error"`），0 choice 作为心跳跳过。
- AI 返回非 200 或 `finish_reason="error"` 时，错误信息不写入 conversation history。

## 其他约定

- AI 连接超时：`ClientTimeout(total=None)` — 语义：不超时，与 channel 一致（由 `AiClient.stream()` 管理）
- 流式 `delta` 字段可能为 `null`（非缺失 key），`AiClient` 用 `isinstance(delta_data, dict)` 校验后产出 `AiDelta`
- Tool 模块在 `sys.modules` 中以 `psi_tool_{name}_{agent_uuid}_{file_hash[:12]}` 注册，同进程多 agent 互不冲突
- Schedule 加载时捕获坏 cron 表达式（不会导致整个 session 启动崩溃）

## 协议适配层

Session 层使用两个对称的协议适配器，将 `SessionAgent.run()` 包裹为纯业务逻辑：

### AiClient（`ai_client.py`）
- 封装 HTTP/SSE 连接管理与原始解析
- `stream(request_body) → AsyncIterator[AiDelta]`
- 处理：非 200、多 choice 错误检测、心跳跳过、`[DONE]` 终止

### ChannelAdapter（`channel_adapter.py`）
- 纯无状态编解码——`parse_request()` 和 `write()` 两个入口
- `parse_request(request) → (user_message, extra_params)` — HTTP JSON 解析
- `write(response, chunks)` — 消费 `AgentChunk` 迭代器，写入 SSE 到 response
- 不持有 agent / lock 引用，不调用 `agent.run()`

### 核心类型
| 类型 | 方向 | 职责 |
|------|------|------|
| `AiDelta` | AI→SessionAgent | SSE 解析后的内部流元素 |
| `AgentChunk` | SessionAgent→Channel | 纯语义输出（仅 content / reasoning） |
| `AgentError` | SessionAgent→Channel | 不可恢复错误信号 |

## SessionAgent 支持多种传输

所有组件通过前缀自动检测传输协议（实现位于 `psi_agent._sockets`）：

`AiClient` 端（`resolve_connector_and_endpoint`）：
- `http(s)://host:port` → `TCPConnector`
- `\\\\.\\pipe\\name` → `NamedPipeConnector`（Windows only）
- 裸文件系统路径 → `UnixConnector`

服务器端（`create_site`）：
- `http(s)://host:port` → `TCPSite`
- `\\\\.\\pipe\\name` → `NamedPipeSite`（Windows only）
- 裸文件系统路径 → `UnixSite`

## Tool 加载约定

- `workspace/tools/*.py` 中的每个 `.py` 文件（不含 `_` 开头）
- 文件中所有非 `_` 开头的 `async def` 函数都会被加载为 tool
- 参数类型必须为 `str`、`int`、`float`、`bool`、`list[X]` 或 `X | None`（`Optional[X]`）
- `*args`、`**kwargs` 和多类型 Union（`int | str`）不支持，抛 `TypeError`
- `get_type_hints()` 解析失败时 `from_callable()` 抛出异常，加载器捕获后跳过该 tool 并打印 error
- 只支持 Google-style docstring（`Args:` 段落，`Returns:` 和 `Yields:` 作为描述结束标记）
- 用 `inspect.signature()` 提取参数（类型注解 → JSON Schema 类型）
- 用 `inspect.getdoc()` 提取描述（支持 Google-style 的 `Args:` 格式）
- 同名 tool 会被跳过并打印 warning（按文件加载顺序）

## 动态重载

SessionAgent 支持运行时增量加载 tools 和 schedules，无需重启：

```python
# agent.reload_tools()   → dict[str, str]  {'echo': 'added', 'bash': 'skipped'}
# agent.reload_schedules() → list[Schedule]
```

- `reload_tools()`：扫描 `workspace/tools/`，SHA256 hash 检测变化，只加载新增或修改的工具
- `reload_schedules()`：扫描 `workspace/schedules/`，按 name 去重，新 schedule 自动在 task group 中启动 runner
- 需要 `Session.run()` 中先调用 `agent.set_task_group(tg)` 注入 task group 才能自动启动 schedule runner

## Tool 调用细节

**参数类型解析**：
由于项目全量使用 `from __future__ import annotations`，函数注解以字符串形式存储。因此 `ToolFunction.from_callable()` 必须用 `typing.get_type_hints()` 解析，**不能**直接读 `param.annotation`。

**流式 Tool Call 累积**：
AI 的 tool_calls 通过 SSE 流式传输——多个 chunk 中的 `delta.tool_calls` 逐步补充同一 index 的参数。Agent 用 `accumulated_tool_calls: dict[int, dict]` 按 index 累积：
- `id`：取第一次非空值
- `function.name`：取第一次非空值
- `function.arguments`：**拼接**所有 partial JSON 片段

同时累积 `reasoning`（AI 的思考过程）——DeepSeek V4 等 reasoning model 要求 tool call 轮次中 `reasoning` 必须完整回传到 API。

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
    async with agent._lock:       ← 等当前请求完成
      调用 agent.run(msg)       ← AI 处理
      流式结果追加到 pending_chunks (list[AgentChunk])
      agent.set_pending_schedule_chunks(chunks)
      ↓
    下次 channel 请求到达时：
      SessionAgent.run() 开头先 yield 所有 _pending_schedule_chunks
      然后正常处理当前 channel 消息
```

关键点：
- Schedule 是纯配置数据类（`name, cron, task_content`），cron 状态由 `run_one_schedule` 维护
- Schedule 响应的 content 和 reasoning 各自存在于各自的消息周期，不会交错
- 多个 schedule 可以并发 sleep，但通过 lock 串行触发
- cron 表达式在加载时验证，非法表达式会导致该 schedule 被跳过

## History 持久化

Session 支持将对话历史持久化到 `workspace/histories/{session_id}.jsonl`：

- `Session.session_id: str | None = None` — None 时自动生成 UUID，给定字符串时可 resume
- 加载：`SessionAgent.create()` 中从 jsonl 逐行读取，非法行跳过 + warning
- 保存：仅在 `finish_reason="stop"` 且 content 成功追加后，覆盖写入整个 history
- error / tool_calls 中间状态 / 异常 → 不写盘
- 首次使用时自动创建 `histories/` 目录 + `.gitignore`（忽略全部文件）
