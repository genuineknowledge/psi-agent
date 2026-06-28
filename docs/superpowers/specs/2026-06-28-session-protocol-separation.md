# Session 协议分离重构

## 动机

当前 `session/agent.py`（577 行）混合了三种职责：AI 侧的 HTTP/SSE 协议解析、Channel 侧的 HTTP/SSE 响应写入、核心 agent loop 业务逻辑。需要将协议层从业务逻辑中剥离，使 `SessionAgent.run()` 成为纯语义驱动的方法，由两个对等的协议适配器（`AiClient` 和 `ChannelAdapter`）包裹。

## 架构

```
Channel Request (HTTP/SSE in)
    │
    ▼
┌─────────────────────────────────────┐
│  ChannelAdapter（channel_adapter.py）│  ← Channel 侧协议
│  · 解析 JSON body → user_message    │
│  · AgentChunk → ChatCompletionChunk │
│  · SSE 写入 response               │
└──────────────┬──────────────────────┘
               │ AgentChunk stream (run 产出)
               ▼
┌─────────────────────────────────────┐
│  SessionAgent.run()（agent.py）      │  ← 纯业务逻辑
│  · system prompt 构建               │
│  · history 管理 + 持久化            │
│  · agent loop 决策                  │
│  · tool call 累积 + 执行             │
└──────────────┬──────────────────────┘
               │ AiDelta stream (run 消费)
               ▼
┌─────────────────────────────────────┐
│  AiClient（ai_client.py）            │  ← AI 侧协议
│  · HTTP/SSE 连接管理                │
│  · 原始 SSE 解析 → AiDelta          │
│  · 非 200 / 多 choice 错误检测      │
└─────────────────────────────────────┘
    │
    ▼
AI Backend (HTTP/SSE)
```

两个 adapter 对称：各自封装一侧协议的编解码。`run()` 居中，只消费 `AiDelta`，只产出 `AgentChunk`。

## 类型层

所有类型保留在 `session/protocol.py`。

### 新类型

**`AiDelta`** — AI client 产出的内部流元素，run() 用它驱动 agent loop：

```python
@dataclass
class AiDelta:
    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[dict] | None = None   # partial, 按 index 累积
    finish_reason: str | None = None
```

**`AgentChunk`** — run() 的对外输出，只含语义内容：

```python
@dataclass
class AgentChunk:
    content: str | None = None
    reasoning: str | None = None
```

- Tool 通知（`[Tool Call: ...]` / `[Tool Result: ...]`）通过 `reasoning` 传递。
- Max rounds exceeded 通过 `content="[Max tool rounds reached]"` 传递。

### 新异常类型

```python
class AgentError(Exception):
    """Raised by run() when the agent encounters an unrecoverable error."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
```

Run() 遇到 `AiDelta(finish_reason="error")` 时 raise `AgentError`，而非 yield AgentChunk。这让错误信号通过异常通道传递，保持 AgentChunk 的纯净——它只承载 content/reasoning 的正常产出。

### 已有类型（不变）

`ChatCompletionChunk`、`StreamChoice`、`DeltaMessage`、`ToolFunction` 保持不变——它们仍是 Channel 与外部世界的协议格式。

## AiClient（`session/ai_client.py`）

```python
class AiClient:
    def __init__(self, ai_socket: str): ...

    async def stream(self, request_body: dict) -> AsyncIterator[AiDelta]:
        """发送请求到 AI backend，yield 解析后的 AiDelta。"""
```

**内部逻辑**（从 `SessionAgent._stream_ai_request` 迁移）：
1. `resolve_connector_and_endpoint(ai_socket)` 建立连接
2. `ClientSession.post(endpoint, json=request_body)` 发送
3. 非 200 → `AiDelta(finish_reason="error", content="[AI Error: {status}]")`
4. 逐行解析 SSE：`data:` → `json.loads`
5. `choices` > 1 → 错误 AiDelta
6. `choices` == 0 → 心跳跳过
7. `delta` 不是 dict → 降级为空 dict
8. 正常 chunk → `AiDelta(content, reasoning, tool_calls, finish_reason)`

**不累积 tool_calls**：partial tool_calls 原样透传，累积逻辑在 run() 中。

**生命周期**：`SessionAgent.create()` 内部创建一次。

## ChannelAdapter（`session/channel_adapter.py`）

```python
class ChannelAdapter:
    @staticmethod
    async def handle(
        request: web.Request, agent: SessionAgent, lock: anyio.Lock
    ) -> web.StreamResponse: ...

    @staticmethod
    async def parse_request(request: web.Request) -> tuple[dict, dict]: ...

    @staticmethod
    async def write_stream(
        chunks: AsyncIterator[AgentChunk], response: web.StreamResponse
    ) -> None: ...

    @staticmethod
    def to_chat_completion_chunk(chunk: AgentChunk) -> ChatCompletionChunk: ...
```

**`handle()`** 是入口：
1. `parse_request()` 提取 `user_message` + `extra_params`（parse 失败直接返回 400 JSON，不进入 lock）
2. 创建 SSE `StreamResponse`
3. `async with lock:` → `response.prepare()`
4. `try:` `agent.run()` iterator → `write_stream()` → `[DONE]`
5. `except AgentError:` → 将 error message 封装为 `ChatCompletionChunk(finish_reason="error")` 写入
6. `except Exception:` → 意外异常同样封 error chunk

**`to_chat_completion_chunk()`** 映射：
- `AgentChunk(content=x)` → `DeltaMessage(content=x)`
- `AgentChunk(reasoning=x)` → `DeltaMessage(reasoning=x)`
- 两者可同时出现在同一个 `ChatCompletionChunk` 中

## SessionAgent 变化（`session/agent.py`）

### `__init__`

- 参数 `ai_socket: str` → `ai_client: AiClient`
- 删除 `_build_connector_and_endpoint()`
- 其余不变

### `create()`

内部 `AiClient(ai_socket)` 创建，传入 constructor。

### `run()`

变化：

1. **输入**：`self._stream_ai_request(body)` → `self._ai_client.stream(body)`
   - 两者都是 `AsyncIterator`，遍历逻辑不变
   - tool_calls 累积、finish_reason 判断保持不变

2. **输出**：`yield ChatCompletionChunk(...)` → `yield AgentChunk(content=..., reasoning=...)`
   - AI content/reasoning → `AgentChunk`
   - Tool 通知 → `AgentChunk(reasoning="[Tool Call: ...]")`
   - Max rounds → `AgentChunk(content="[Max tool rounds reached]")`

3. **Error 信号**：`AiDelta(finish_reason="error")` 时 `raise AgentError(message)`，不再 yield

4. **删除**：`_stream_ai_request` 方法

run() 内部不再 import 或引用 `ChatCompletionChunk`、`StreamChoice`、`DeltaMessage`。

### `handle_chat_completions`

从 `SessionAgent` 中**移除**。由 `ChannelAdapter.handle()` 接管。

### `set_pending_schedule_chunks`

参数类型从 `list[ChatCompletionChunk]` 改为 `list[AgentChunk]`。内部 `_pending_schedule_chunks` 同改。

## 编排层变化

### `session/__init__.py`

```python
async def run(self) -> None:
    setup_logging(verbose=self.verbose)

    workspace_path = ...
    agent = await SessionAgent.create(
        ai_socket=self.ai_socket,
        workspace_path=workspace_path,
        max_tool_rounds=self.max_tool_rounds,
        session_id=self.session_id,
    )

    lock = anyio.Lock()

    async def channel_handler(request: web.Request) -> web.StreamResponse:
        return await ChannelAdapter.handle(request, agent, lock)

    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(serve_session, channel_socket=self.channel_socket, handler=channel_handler))
        for schedule in agent.schedules:
            tg.start_soon(partial(run_one_schedule, schedule, agent, lock))
```

### `session/server.py`

`serve_session` 不再需要 `app["lock"]`——handler 闭包已捕获。

### `session/scheduler.py`

`run_one_schedule` 消费 `AgentChunk` 替代 `ChatCompletionChunk`。

## 文件变动汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `session/ai_client.py` | **新建** | AI 侧协议适配器 |
| `session/channel_adapter.py` | **新建** | Channel 侧协议适配器 |
| `session/protocol.py` | **修改** | 新增 `AiDelta`、`AgentChunk`、`AgentError` |
| `session/agent.py` | **修改** | 删除 `_stream_ai_request`、`handle_chat_completions`；`run()` yield `AgentChunk`；`__init__` 接受 `AiClient` |
| `session/__init__.py` | **修改** | handler 闭包捕获 lock + agent；引入 `ChannelAdapter` |
| `session/server.py` | **修改** | 去掉 `app["lock"]` |
| `session/scheduler.py` | **修改** | `ChatCompletionChunk` → `AgentChunk` |

## 测试影响

- `test_agent.py`：需 mock `AiClient`（注入 yield `AiDelta` 序列），验证 `run()` 产出 `AgentChunk`
- `test_server.py`：需 adapt `ChannelAdapter.handle` 的调用形式
- `test_session.py` / `test_session_*` 集成测试：整体行为不变，接口适配
- `test_scheduler.py`：`AgentChunk` 替代 `ChatCompletionChunk`

## 不变量

- Agent loop 逻辑不改变（tool_calls 累积、finish_reason 分支、history 管理）
- History 持久化逻辑不改变
- Schedule 机制不改变
- SSE 流式行为不改变（Channel 客户端无感知）
- Lock 并发控制语义不改变
- System prompt 惰性构建不改变
