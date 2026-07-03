# Session 层设计文档

## 概述

Session 层是 psi-agent 的核心——负责 workspace 解析、agent loop、tool 执行、schedule 调度以及面向 Channel 的 HTTP/SSE 服务。

## Workspace 启动流程

`Session.run()` 的启动顺序（由 `SessionAgent.create()` 完成 workspace 加载）：

```
1. setup_logging(verbose)
2. 解析 workspace 路径（空字符串时用 Path.cwd()，否则 anyio.Path.resolve()）
3. SessionAgent.create() → 生成 session_id、创建 AiClient/ChannelAdapter/anyio.Lock、加载 tools/schedules/system 模块
4. 启动 anyio.task_group：
   ├── serve_session(agent=agent)  ← 从 agent 读取 channel_socket + handle_request
   └── 每个 schedule 一个 run_one_schedule(schedule, agent) task

**关键点**：
- `SessionAgent` 自包含：持有 `_ai_client`、`_channel_adapter`、`_lock`
- `_session_id` 从 `_history_path.stem` 派生，同时用于 sys.modules 隔离（tools/system 的 module name）
- `channel_socket` 由 `Session.run()` 直接传给 `serve_session()`，不进入 agent 内部
- 所有手动模块加载使用 `原名_session_id_文件hash` 作为 module name（tool 和 system prompt 均用 `compile` + `exec` 避免 importlib bytecode 缓存），确保同进程多 session 隔离
- `SessionAgent.create()` 完成所有初始化——`__init__.py` 只做入口编排
- Tool 加载：`compile(source)` + `exec(module.__dict__)` 避免 importlib 的 bytecode 缓存导致刷新时读到旧文件内容
- System prompt 在首次 `run()` 调用时惰性构建（通过 `system_prompt_builder`）
- 后续请求可调用 `system_prompt_rebuild_checker()`（如果定义），返回 True 则重建 system prompt

## Agent Loop 逻辑

1. 收到 channel 请求 → `ChannelAdapter.handle()` 解析请求，提取 user_message + extra_params
2. `SessionAgent.run()` 入口：
   - add() / replace_system() 在首次变更时自动建立快照（implicit snapshot）
   - 惰性构建或重建 system prompt（首次 run 或 rebuild checker 返回 True 时）
   - 检查暂存的 schedule 响应 → peek + yield → yield 全部成功后 `clear_pending()`
   - User message 追加到 history 后立即 ``commit()`` 落盘
3. 获取 `anyio.Lock`（忙则 FIFO 排队等待）—— `handle_request()` 在调用 `run()` 前持有
4. 通过 `AiClient.stream()` 发送 `history + tools + extra_params` 到 AI backend（streaming）
5. 消费 `AiDelta` 流（AiClient 已做好 SSE 解析、错误检测）：
   - content → `yield AgentChunk(content=...)` 给 ChannelAdapter
   - reasoning → `yield AgentChunk(reasoning=...)` 给 ChannelAdapter
   - tool_calls → 累积（按 index 拼接 partial JSON）
   - `finish_reason="tool_calls"` → 执行 tool → 结果追加到 history → 回到步骤 4
   - finish_reason="stop" → 最终 content 追加到 history + `commit()` → 释放锁
   - finish_reason="error" → 回滚到快照 → `raise AgentError(message)`
   - 任何未捕获异常 → 回滚到快照 → 向上传播
6. 最多 `max_tool_rounds` 轮 tool call，达到上限时追加关闭 assistant 消息 + commit
7. **Turn 级别原子性**：``run()`` 所有正常出口调用 ``commit()``（save + clear snapshot）；异常时 ``async with`` 上下文管理器自动 ``rollback()``。内存和磁盘仅在同一检查点同步更新。

**注意**：
- Channel 不发送 history。每次请求只带最新一条 user message，Session 自己维护完整 history。
- `response.prepare()` 在 lock 内执行——客户端在 lock 释放前不会看到 HTTP 200。
- `SessionAgent.handle_request()` 编排完整请求生命周期：parse → lock+prepare → run → write。
- `ChannelAdapter` 是纯无状态工具——不持有 agent/lock 引用。
- Channel 请求中除 `messages` 外的不认识参数全部透传到 AI 层（`extra_params`）。
- AI 返回多 choice 时报错（`finish_reason="error"`），0 choice 作为心跳跳过。
- AI 返回非 200 或 `finish_reason="error"` 时，错误信息不写入 conversation history，且通过 turn 快照回滚机制保证本轮用户消息也不落盘。

## 其他约定

- AI 连接超时：`ClientTimeout(total=None)` — 语义：不超时，与 channel 一致（由 `AiClient.stream()` 管理）
- 流式 `delta` 字段可能为 `null`（非缺失 key），`AiClient` 用 `isinstance(delta_data, dict)` 校验后产出 `AiDelta`
- Tool 模块在 `sys.modules` 中以 `psi_tool_{name}_{session_id}_{file_hash}` 注册（完整 64 位 SHA-256 hash，不截断），同进程多 session 互不冲突
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
- 内部以 per-file 结构存储（`FileEntry` dataclass），包含 `file_hash`、`tools`（ToolFunction dict）、`funcs`（callable dict）、`fresh`（是否本次导入）
- `ToolRegistry.tools` 为 `@property`，展平所有 `FileEntry` 为 `dict[str, ToolFunction]`
- 参数类型必须为 `str`、`int`、`float`、`bool`、`list[X]` 或 `X | None`（`Optional[X]`）
- `*args`、`**kwargs` 和多类型 Union（`int | str`）不支持，抛 `TypeError`
- `get_type_hints()` 解析失败时 `from_callable()` 抛出异常，加载器捕获后跳过该 tool 并打印 error
- 只支持 Google-style docstring（`Args:` 段落，`Returns:` 和 `Yields:` 作为描述结束标记）
- 用 `inspect.signature()` 提取参数（类型注解 → JSON Schema 类型）
- 用 `inspect.getdoc()` 提取描述（支持 Google-style 的 `Args:` 格式）
- 跨文件同名 tool 以后加载者覆盖（`tools` property 展平时 `dict.update` 自然行为）

## 动态重载

`ToolRegistry.refresh(session_id)` 在每次 agent turn 前自动调用，检测文件变更并增量更新：

```python
# refresh() → dict[str, str]  {'echo': 'added', 'bash': 'skipped'}
```

- 扫描 `workspace/tools/`，按 `FileEntry.file_hash` 检测变更：
  - hash 不变 → 复制旧 FileEntry（`fresh=False`），tool 标记为 `skipped`
  - hash 变化 → 重新 `compile` + `exec`（`fresh=True`）
  - 新文件 → 导入并标记 `added`
  - 文件删除 → 其所有 tool 标记 `removed`
  - 文件内 tool 增删 → 分别标记 `added` / `removed`
- `fresh` 标志保证 skipped 文件不被误删
- `ScheduleRegistry` 以 per-file `ScheduleEntry` 存储（含 hash），`refresh()` 支持 add/update/remove/skip。每个 schedule 有独立 `CancelScope`，update/remove 时取消旧 runner 并启动新 runner

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
- **Turn 级别原子性**：`SessionAgent.run()` 每次调用通过 ``async with self._conversation`` 进入上下文管理器，首次 `add()` / `replace_system()` 自动建立快照。user message 追加后立即 `commit()`（早期落盘，崩溃恢复基线），后续仅在对 AI 响应成功的检查点再次 `commit()` 更新；任何异常（AI error、连接断开、cancellation）都会通过 ``__aexit__`` 自动触发 `Conversation.rollback()` 恢复到快照，保证内存和磁盘始终同步于最近一个成功阶段。
- 保存时机（一致性检查点）：
  - `finish_reason="stop"` — assistant 响应追加后立即 `commit()`（完整回合）
  - `finish_reason="tool_calls"` — 所有 tool 结果追加后立即 `commit()`（子回合）
  - unexpected `finish_reason` — 累积 content 追加后 `commit()`
  - 达到 `max_tool_rounds` — 追加 `[Max tool rounds reached]` assistant 消息后 `commit()`
- `Conversation.save()` 使用 tempfile + `os.replace()` 实现原子写入；`commit()` 封装 save + 清除快照
- **部分保存**的场景：`finish_reason="error"`、AI 连接断开、channel 断开、schedule runner 异常——user message 已通过早期 `commit()` 落盘，AI 响应部分通过 `rollback()` 回滚，不写入磁盘
- 首次使用时自动创建 `histories/` 目录 + `.gitignore`（忽略全部文件）

### peek_pending / clear_pending 安全机制

`Conversation.peek_pending()` 返回 pending chunks 的副本但**不清空** buffer——调用方在 yield 全部成功后显式调用 `clear_pending()`。这保证 channel 断开时 pending schedule chunks 不会永久丢失，下次请求会重新 push。
