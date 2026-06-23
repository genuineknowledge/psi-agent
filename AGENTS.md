# AGENTS.md

本文档面向后续开发者（人或 AI Agent），说明 psi-agent 的设计思路、代码结构、开发约定以及我们在开发过程中沉淀的最佳实践。

## 设计理念

psi-agent 是一个**微内核**式的 agent 框架。核心理念是：

1. **最小化核心**: 框架本身只提供通信协议、组件组合和 tool/Schedule 加载机制
2. **功能由 workspace 定义**: agent 的能力（tools、system prompt、定时任务）完全由 workspace 目录中的文件定义
3. **组件无状态**: AI 后端不保存任何状态；Session 只维护一个内存中的 history；Channel 不管理历史
4. **组合优于继承**: 三个独立组件通过 Unix socket 任意组合
5. **一切异步**: 所有 IO 操作使用 `anyio`，永不使用 `asyncio` 原生 API 或 `pathlib`
6. **零抑制**: 不堆 `noqa`，不设 `per-file-ignores`。代码本身应符合规则

## 架构决策记录

以下是设计过程中有意为之的关键决策：

**为什么用 Unix socket 而非 TCP？**
Socket 文件天然隔离——不同项目用不同文件路径，互不干扰。没有端口冲突，没有防火墙问题。本地组件通信不需要网络栈开销。

**为什么 AI 是 Server、Session 是 Client？**
AI 后端无状态，不保存任何信息。多个 Session 可以共享同一个 AI backend。如果反过来（Session 是 Server），每个 Session 都要自行配置上游 API，违反"组合"原则。

**为什么 Session 不持久化 history？**
微内核原则：核心只做通信和路由。history 是实现细节，不应成为框架的一部分。未来可以加可选的持久化插件，但内核本身体积不应膨胀。

**为什么 socket 文件不自动 unlink？**
支持热换 Server。每个 `session.post()` 新建 TCP/Unix 连接，由 `UnixConnector` 按路径重新 connect。只要新的服务进程绑定到同一 socket 路径，客户端无需重启即可继续通信。auto-unlink 会破坏这个能力——socket 文件需要保留，由新进程手动接管。

## 技术栈

| 领域 | 技术 |
|------|------|
| 异步 | `anyio`（禁止使用 `asyncio` 原生 API、`pathlib`） |
| HTTP | `aiohttp`（Unix socket + TCP） |
| CLI | `tyro`（Union dataclasses + 嵌套子命令） |
| REPL | `prompt-toolkit`（multiline async prompt）+ `rich`（终端格式化） |
| 日志 | `loguru` |
| Lint/Format | `ruff` |
| 类型检查 | `ty`（Astral 出品，Rust 实现） |
| 测试 | `pytest` + `pytest-asyncio`（anyio mode） |
| 构建 | `uv` + `hatchling` + `hatch-vcs` |
| Python | >= 3.14 |

## 代码结构

```
src/
└── psi_agent/
    ├── cli.py                  # tyro CLI 入口，定义 top-level Union
    ├── _yaml.py               # 共享 YAML header 解析（scheduler + workspace system.py）
    ├── _logging.py              # loguru 配置，verbose→DEBUG
    ├── ai/
    │   ├── common.py               # AI 后端共享（ErrorResponse + SSEChunk + serve_ai_backend）
    │   ├── openai_completions/     # OpenAI→OpenAI 透传后端
    │   └── anthropic_messages/     # Anthropic→OpenAI 转换后端（含 thinking 转换）
    ├── session/
    │   ├── __init__.py             # Session dataclass + run()，workspace 加载入口
    │   ├── server.py               # channel 端 aiohttp server，单锁串行
    │   ├── agent.py                # 核心 agent loop（history + tool call + streaming）
    │   ├── protocol.py             # Session 层协议类型（ChatCompletionChunk 等）
    │   ├── tools.py                # workspace tools 加载（async anyio.Path）
    │   └── scheduler.py            # cron-based 定时任务（croniter）
    └── channel/
        ├── repl/                   # 交互式 REPL（Rich Console + prompt_toolkit multiline）
        ├── web/                    # 网页端聊天（REPL 的浏览器接口：aiohttp 静态页 + /api/chat SSE 转发）
        └── cli/                    # 单次消息 CLI（Rich 格式化输出）
```

项目使用 **src-layout**（`src/psi_agent/`），由 `uv sync` 安装为 editable package。

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

## 核心通信协议

所有组件通过 **aiohttp Unix socket** 以 **OpenAI Chat Completions HTTP/SSE** 格式通信：

- **AI socket**: Session 作为客户端访问，`POST /v1/chat/completions`
- **Channel socket**: Session 作为服务端，`POST /v1/chat/completions`

SSE 流中的特殊字段：
- `delta.content` — AI 最终文本回复
- `delta.reasoning_content` — 聚合了 AI thinking + tool_call 意图 + tool_call 结果
- `delta.tool_calls` — 部分 tool call 定义（流式累积）

错误响应有两种形式：

1. **非流式（HTTP 层面）**：请求解析失败等，在 `response.prepare()` 之前返回
   ```json
   {"error": {"message": "...", "type": "...", "code": "..."}}
   ```

2. **流式（SSE 层面）**：已 commit HTTP 200 后发生的错误（上游异常、连接断开等），使用 ChatCompletionChunk 格式
   ```json
   {"id": "error", "choices": [{"index": 0, "delta": {"content": "[Upstream Error 401]: ..."}, "finish_reason": "error"}]}
   ```
   所有层统一使用 `finish_reason="error"` 标记流式错误，Session 检测到后不写入 conversation history。

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
7. 最多 10 轮 tool call

**注意**：
- Channel 不发送 history。每次请求只带最新一条 user message，Session 自己维护完整 history。
- `response.prepare()` 在 lock 内执行——客户端在 lock 释放前不会看到 HTTP 200，避免"快速返回 200 然后 hang"的误导行为。
- AI 返回非 200 错误时，错误信息不会写入 conversation history（`finish_reason="error"`），避免污染后续对话。

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

## Anthropic 转换细节

- System message → Anthropic `system` 字段
- Assistant tool_calls → Anthropic `tool_use` content blocks
- Tool result → Anthropic `tool_result` content blocks
- Anthropic `thinking_delta` → OpenAI `reasoning_content`
- Anthropic `text_delta` → OpenAI `content`
- Anthropic `input_json_delta` → OpenAI partial `tool_calls` delta

**已知局限**：`content_block_stop` 事件未处理，导致 Anthropic 流中的 tool_use 完成信号无法映射为 OpenAI 的 `finish_reason="tool_calls"`。当前通过最终的 `message_stop` 事件发送 `finish_reason="stop"` 来结束。该局限已列入未来扩展方向。

## AI 层行为约定

- **Model 参数覆盖**：AI 层收到 body 中的 `model` 字段会被**忽略**，统一替换为启动时配置的 `--model`。这是有意设计——AI 层就是用来隐藏上游 model 细节的
- **错误透传**：上游返回的非 200 响应，错误信息通过 SSE error JSON 格式透传给下游
- **SSE 行透传**：openai-completions 模式下上游 SSE 行原样透传（不做格式转换）
- **API Key 环境变量**：`--api-key` 为可选参数，若不提供则自动从环境变量 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 读取。`--model` 和 `--base-url` 同样支持对应的环境变量 fallback

## SessionAgent 支持 TCP URL

`SessionAgent.ai_socket` 支持两种形式：
- Unix socket 路径 → 使用 `UnixConnector`
- `http://` / `https://` 开头的 TCP URL → 使用 `TCPConnector`

这允许测试中使用 TCP mock server 代替 Unix socket。

## 日志约定

- 所有模块使用 `from loguru import logger`
- 默认 INFO 级别，`--verbose` 开启 DEBUG
- DEBUG 必须覆盖：每个 SSE chunk、tool 执行、锁获取/释放
- 格式：`时间 | 级别 | 模块:函数:行号 - 消息`
- Channel 客户端使用 `rich.console.Console` 做终端输出，**禁止使用 `print()`**

## Channel 终端输出约定

- Channel 客户端（repl、cli）是终端 UI 程序，需要格式化输出
- **使用 `rich.console.Console`** 替代 `print()`
- 思考过程（reasoning_content）：`console.print(..., style="dim")`
- 错误信息：`console.print("[red]Error: ...[/red]")`
- REPL 欢迎页：`console.print(Panel(...))`
- **`Console(highlight=False)`**：禁用自动语法高亮，避免 Rich 误把 AI 回复当代码着色
- **整个仓库不允许 `print()`**——T20 (flake8-print) 规则强制，无 per-file-ignore

## REPL 约定

- 使用 `prompt_toolkit` 的 `PromptSession(multiline=True)`
- `Enter` 换行，`Alt+Enter`（Escape+Enter）发送
- PS1: `> `，PS2: `. `（同宽对齐）
- `Ctrl+D` 退出

## 关键注意事项（踩坑经验）

以下是开发过程中遇到的、容易忽略或出错的点：

1. **Socket 文件残留**：进程退出后 `.sock` 文件不会自动删除。重启时必须先 `rm` 或 `unlink()`。测试中 `tmp_path` 自动清理，生产环境需自行管理

2. **`anyio.Path` vs `pathlib.Path`**：两者不兼容。`anyio.Path` 的 IO 方法（`exists()`, `read_text()`, `glob()`）需要 `await`。需要 `pathlib.Path` 时用 `Path(str(anyio_path))` 转换，反之用 `anyio.Path(str(pathlib_path))`

3. **stderr PIPE 阻塞**：`subprocess.PIPE` 必须消费完内容，否则子进程 hang。已全面改用 `anyio.open_process`，其 stderr 为异步流

4. **Subprocess 替代方案**：任何时候都不要在 async 函数中直接调用 `subprocess.Popen` / `subprocess.run` / `time.sleep` / `Path.exists()`。对应替代：
   | 同步 API | 异步替代 |
   |----------|----------|
   | `subprocess.Popen()` | `await anyio.open_process()` |
   | `subprocess.run()` | `await anyio.run_process()` |
   | `time.sleep()` | `await anyio.sleep()` |
   | `Path.exists()` | `await anyio.Path().exists()` |

5. **System prompt 容错**：`system_prompt_builder()` 可能抛异常或返回 None。启动阶段必须 catch 异常，不影响 session 启动（此时 system_prompt 为 None）

6. **Tool 函数必须 awaitable**：`load_tools_from_workspace` 只加载 `async def` 函数。普通函数会被跳过并打印 warning

## 测试约定

- **功能项实际部署测试**: 所有功能项改动在合并、交付或标记完成前，必须经过目标环境的实际部署测试。不能只用本地单测、mock 集成测试、lint、类型检查或代码审查替代实际部署验证。
- **部署测试最低要求**: 部署目标 commit 到 PVE/目标运行环境，重启或热更新相关服务，通过真实前台入口（CLI、微信或对应 channel）触发新功能，并核验服务状态、日志、生成产物和用户隔离边界。
- **结果记录**: 最终回复必须说明实际部署测试的环境、命令/入口、测试问题、观察到的结果，以及未覆盖项。若确实无法完成实际部署测试，必须明确说明阻塞原因，不能把该功能项报告为已完成。
- **框架**: `pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`，anyio backend）
- **异步测试**: `@pytest.mark.anyio`
- **测试目录结构**: 镜像 `src/psi_agent/`
- **集成测试**: 独立目录 `tests/integration/`，用 `tests/__init__.py` 和 `tests/integration/__init__.py` 使 test 目录成为 package
- **无需 conftest path hack**: `uv sync` 将 psi-agent 安装为 editable package，`import psi_agent` 直接可用
- **Mock AI socket**: `aiohttp.web.Application` + `UnixSite`/`SockSite`（获取随机端口用预绑定 socket）
- **真实 API 测试**: 通过环境变量 `PSI_TEST_OPENAI_*` / `PSI_TEST_ANTHROPIC_*` 注入凭证，未设置时自动 skip
- **所有 async 操作使用 anyio**: 禁止在 async 上下文中直接调用 `subprocess`、`time.sleep`、`pathlib.Path` 方法。详见上方"关键注意事项"第 4 条

### 集成测试 Mock Server

- `MockAIServer` 在 conftest.py 中定义，通过 pytest fixture 提供
- Mock server **对每个请求返回完全相同的 chunks 列表**。需要 per-request 差异化响应时，使用 inline mock server + `nonlocal` 计数器

示例——per-request 差异化：

```python
req_count = 0
async def handler(request):
    nonlocal req_count
    req_count += 1
    if req_count == 1:
        # 返回 tool_calls
    else:
        # 返回最终文本
```

- 集成测试中 `assert _wait_for_socket()` 会轮询直到 socket 创建。注意 socket 创建 ≠ 服务就绪，需要额外 `await anyio.sleep(0.3)` 确保 accept 就绪

## Lint / Type Check 约定

- **ruff**: `select = ["E", "F", "I", "W", "UP", "ASYNC", "SIM", "C4", "B", "RUF", "N", "T20", "PLC"]`
- **ty**: 全局 `ty check .`
- **per-file-ignores**: **零条**。所有代码通过自身符合规则，不靠抑制
- **仅 2 处 ty:ignore**（无法避免）：
  - `cli.py:29` — tyro.cli() 的 `Annotated[Union[...]]` 类型推断局限
  - `conftest.py:109` — pytest async generator fixture 的返回类型局限（`yield` 导致函数被推断为 AsyncGenerator，与标注的 MockAIServer 冲突）

`cast` 不能解决 conftest 的问题——`cast` 是表达式级工具，无法修改 async generator 函数的返回类型。`# ty: ignore` 是正确的标准解法。

## 类型注解约定

- 使用 `from __future__ import annotations` 在所有文件
- `X | None` 而非 `Optional[X]`
- `list[X]` 而非 `List[X]`（Python 3.14 原生）
- 禁止使用 raw `any`——始终用 `typing.Any`
- `anyio.abc.ByteStream` → 用 `Any` 代替（ty 不识别的第三方类型）

## 开发命令

```bash
uv run ruff check .              # lint 检查
uv run ruff check --fix .        # auto-fix
uv run ruff format .             # 格式化
uv run ruff format --check .     # 格式检查
uv run ty check                  # 类型检查
uv run pytest -v                 # 全部测试
uv run psi-agent --help          # CLI 帮助
uv build                         # 构建
```

## 未来扩展方向

- [ ] 单进程中运行多个 session 实例（利用 anyio task group）
- [ ] workspace.py 统一 workspace 管理
- [ ] 更多 channel 类型（WebSocket、HTTP API 等）
- [ ] 更多 AI 后端（Gemini、本地模型等）
- [ ] Session history 持久化（可选）
- [ ] Channel 广播/多客户端队列
- [ ] Anthropic `content_block_stop` 的 tool_calls finish_reason 转换
