# Session 层设计文档

## 概述

Session 层是 psi-agent 的核心——负责 workspace 解析、agent loop、tool 执行、schedule 调度以及面向 Channel 的 HTTP/SSE 服务。

## Workspace 启动流程

`Session.run()` 的启动顺序（该顺序有依赖关系，不可随意调整）：

```
1. setup_logging(verbose)
2. 解析 workspace 路径（anyio.Path.resolve()）
3. load_tools_from_workspace(tools_dir)     → tools 元数据（name, description, parameters）
4. load_schedules_from_workspace(...)       → Schedule 列表
5. 加载 system_prompt_builder()             → 调用 -> system_prompt 字符串
6. 创建 SessionAgent(..., system_prompt)
7. 注册 tool callables：重新遍历 tools/*.py，找到 async 函数并 agent.register_tool_func(name, func)
8. 创建 anyio.Lock()
9. 启动 anyio.task_group：
   ├── serve_session(...): aiohttp Unix socket server
   └── 每个 schedule 一个独立 anyio task（各自 sleep + 触发）
```

**关键点**：tool 的注册分两阶段——
- `load_tools_from_workspace()` 解析**元数据**（给 AI 看的 function definition）
- `register_tool_func()` 绑定**实际 callable**（session 执行时调用）

两者都在 `run()` 中完成，使用同一个 `workspace/tools/*.py` 文件列表，但加载目的不同。

## Agent Loop 逻辑

1. 收到 channel 请求
2. 检查暂存的 schedule 响应 → 有则先流式返回
3. 获取 `anyio.Lock`（忙则 FIFO 排队等待）
4. User message 追加到 history
5. 发送 `history + tools` 到 AI socket（streaming）
6. 解析 SSE 流：
   - content → yield 给 channel
   - reasoning_content → yield 给 channel
   - tool_calls → 累积（按 index 拼接 partial JSON）
   - finish_reason="tool_calls" → 执行 tool → 结果追加到 history → 回到步骤 5
   - finish_reason="stop" → 最终 content 追加到 history → 释放锁
   - finish_reason="error" → 不写入 history，直接返回
7. 最多 10 轮 tool call

**注意**：
- Channel 不发送 history。每次请求只带最新一条 user message，Session 自己维护完整 history。
- `response.prepare()` 在 lock 内执行——客户端在 lock 释放前不会看到 HTTP 200，避免"快速返回 200 然后 hang"的误导行为。
- AI 返回非 200 错误时，错误信息不会写入 conversation history（`finish_reason="error"`），避免污染后续对话。

## SessionAgent 支持 TCP URL

`SessionAgent.ai_socket` 支持两种形式：
- Unix socket 路径 → 使用 `UnixConnector`
- `http://` / `https://` 开头的 TCP URL → 使用 `TCPConnector`

这允许测试中使用 TCP mock server 代替 Unix socket。

## Tool 加载约定

- `workspace/tools/*.py` 中的每个 `.py` 文件
- 找到**与文件名同名**的 `async def` 函数
- 用 `inspect.signature()` 提取参数（类型注解 → JSON Schema 类型）
- 用 `inspect.getdoc()` 提取描述（支持 Google-style 的 `Args:` 格式）
- 函数必须是 async、非私有（`_` 开头跳过）
- Tool 加载使用 `anyio.Path`，全链路 async

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
每个 schedule 一个独立 anyio task：
  while True:
    croniter.get_next_run()         ← 计算下次触发时间
    await anyio.sleep(触发时间 - now) ← 睡到触发
    if schedule.should_run_now():   ← 双重确认
      schedule.mark_run()
      用 schedule.to_user_message() 构造带标注的 user msg
        ↓
      async with lock:              ← 等当前请求完成
        调用 agent.run(msg)         ← AI 处理
        流式结果追加到 pending_chunks
        agent.set_pending_schedule_chunks(chunks)
        ↓
      下次 channel 请求到达时：
        SessionAgent.run() 开头先 yield 所有 _pending_schedule_chunks
        然后正常处理当前 channel 消息
```

关键点：
- Schedule 响应的 content 和 reasoning_content 各自存在于各自的消息周期，不会交错
- 多个 schedule 可以并发 sleep，但通过 lock 串行触发
- cron 表达式非法时，该 schedule task 每 60s 重试一次
