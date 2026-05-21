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
    ├── logging.py              # loguru 配置，verbose→DEBUG
    ├── protocol.py             # OpenAI 兼容协议 dataclass
    ├── ai/
    │   ├── openai_completions/ # OpenAI→OpenAI 透传后端
    │   └── anthropic_messages/ # Anthropic→OpenAI 转换后端（含 thinking 转换）
    ├── session/
    │   ├── __init__.py         # Session dataclass + run()，workspace 加载入口
    │   ├── server.py           # channel 端 aiohttp server，单锁串行
    │   ├── agent.py            # 核心 agent loop（history + tool call + streaming）
    │   ├── tools.py            # workspace tools 加载（async anyio.Path）
    │   └── scheduler.py        # cron-based 定时任务（croniter）
    └── channel/
        ├── repl/               # 交互式 REPL（Rich Console + prompt_toolkit multiline）
        └── cli/                # 单次消息 CLI（Rich 格式化输出）
```

项目使用 **src-layout**（`src/psi_agent/`），由 `uv sync` 安装为 editable package。

## 核心通信协议

所有组件通过 **aiohttp Unix socket** 以 **OpenAI Chat Completions HTTP/SSE** 格式通信：

- **AI socket**: Session 作为客户端访问，`POST /v1/chat/completions`
- **Channel socket**: Session 作为服务端，`POST /v1/chat/completions`

SSE 流中的特殊字段：
- `delta.content` — AI 最终文本回复
- `delta.reasoning_content` — 聚合了 AI thinking + tool_call 意图 + tool_call 结果
- `delta.tool_calls` — 部分 tool call 定义（流式累积）

错误响应格式（OpenAI 风格）：
```json
{"error": {"message": "...", "type": "...", "code": "..."}}
```

## Agent Loop 逻辑

1. 收到 channel 请求
2. 检查暂存的 schedule 响应 → 有则先流式返回
3. 获取 `anyio.Lock`（忙则返回 503 + error JSON）
4. User message 追加到 history
5. 发送 `history + tools` 到 AI socket（streaming）
6. 解析 SSE 流：
   - content → yield 给 channel
   - reasoning_content → yield 给 channel
   - tool_calls → 累积
   - finish_reason="tool_calls" → 执行 tool → 结果追加到 history → 回到步骤 5
   - finish_reason="stop" → 最终 content 追加到 history → 释放锁
7. 最多 10 轮 tool call

## Tool 加载约定

- `workspace/tools/*.py` 中的每个 `.py` 文件
- 找到**与文件名同名**的 `async def` 函数
- 用 `inspect.signature()` 提取参数（类型注解 → JSON Schema 类型）
- 用 `inspect.getdoc()` 提取描述（支持 Google-style 的 `Args:` 格式）
- 函数必须是 async、非私有（`_` 开头跳过）
- Tool 加载使用 `anyio.Path`，全链路 async

## Anthropic 转换细节

- System message → Anthropic `system` 字段
- Assistant tool_calls → Anthropic `tool_use` content blocks
- Tool result → Anthropic `tool_result` content blocks
- Anthropic `thinking_delta` → OpenAI `reasoning_content`
- Anthropic `text_delta` → OpenAI `content`
- Anthropic `input_json_delta` → OpenAI partial `tool_calls` delta

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
- **整个仓库不允许 `print()`**——T20 (flake8-print) 规则强制，无 per-file-ignore

## REPL 约定

- 使用 `prompt_toolkit` 的 `PromptSession(multiline=True)`
- `Enter` 换行，`Alt+Enter`（Escape+Enter）发送
- PS1: `> `，PS2: `. `（同宽对齐）
- `Ctrl+D` 退出

## 测试约定

- **框架**: `pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`，anyio backend）
- **异步测试**: `@pytest.mark.anyio`
- **测试目录结构**: 镜像 `src/psi_agent/`
- **集成测试**: 独立目录 `tests/integration/`，用 `tests/__init__.py` 和 `tests/integration/__init__.py` 使 test 目录成为 package
- **无需 conftest path hack**: `uv sync` 将 psi-agent 安装为 editable package，`import psi_agent` 直接可用
- **Mock AI socket**: `aiohttp.web.Application` + `UnixSite`/`SockSite`（获取随机端口用预绑定 socket）
- **真实 API 测试**: 通过环境变量 `PSI_TEST_*` 注入凭证，未设置时自动 skip
- **所有 async 操作使用 anyio**: `anyio.open_process` 代替 `subprocess.Popen`，`anyio.Path.exists()` 代替 `pathlib.Path.exists()`，`anyio.sleep()` 代替 `time.sleep()`

## Lint / Type Check 约定

- **ruff**: `select = ["E", "F", "I", "W", "UP", "ASYNC", "SIM", "C4", "B", "RUF", "N", "T20"]`
- **ty**: 全局 `ty check .`
- **per-file-ignores**: **零条**。所有代码通过自身符合规则，不靠抑制
- **仅 2 处 ty:ignore**（无法避免）：
  - `cli.py:29` — tyro.cli() 的 `Annotated[Union[...]]` 类型推断局限
  - `conftest.py:109` — pytest async generator fixture 的返回类型局限

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
