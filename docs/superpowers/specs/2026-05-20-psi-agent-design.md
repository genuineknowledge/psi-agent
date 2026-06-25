# psi-agent 微内核 Agent 框架设计规格

**日期**: 2026-05-20
**状态**: 已审批

---

## 1. 项目概述

psi-agent 是一个"微内核"式的 Python agent 框架。三个独立组件——ai、session、channel——通过 Unix domain socket 以 OpenAI-compatible HTTP/SSE 协议通信。组件无状态，可任意组合。

**核心原则**：
- 微内核：核心极简，功能由 workspace 定义
- 无状态组件，所有 IO 异步（anyio，永不使用 pathlib）
- 充分日志，一切可观测（loguru）
- 现代 Python 3.14+，无需历史包袱

---

## 2. 技术栈

| 领域 | 技术 |
|------|------|
| 异步 IO | `anyio`（禁止使用 `asyncio` 原生 API、`pathlib`） |
| HTTP 客户端 | `aiohttp` |
| CLI | `tyro`（Union dataclasses + 嵌套子命令） |
| CLI REPL | `prompt-toolkit`（async `prompt_async`）+ `rich`（终端格式化） |
| 日志 | `loguru` |
| Lint/Format | `ruff` |
| 类型检查 | `ty`（Astral 出品，Rust 实现） |
| 版本管理 | `hatch-vcs`（从 git tag 动态生成） |
| 测试 | `pytest` + `pytest-asyncio`（anyio mode） |
| 构建 | `uv` + `hatchling` |
| Python | >= 3.14 |

---

## 3. 项目结构

```
psi/
├── pyproject.toml
├── README.md                       # 中文
├── AGENTS.md                       # 中文
├── src/
│   └── psi_agent/
│   ├── __init__.py
│   ├── cli.py                      # tyro CLI 入口
│   ├── _yaml.py                    # 共享 YAML header 解析
│   ├── _socket.py                  # 共享 socket 工具（prefix-based transport 解析）
│   ├── _run.py                     # YAML 批量启动（psi-agent run config.yml）
│   ├── _logging.py                  # loguru 配置
│   ├── ai/  (统一多 provider，基于 any-llm-sdk)
│   │   ├── __init__.py
│   │       └── server.py           # aiohttp HTTP/SSE handler
│   ├── session/
│   │   ├── __init__.py             # Session dataclass + run()
│   │   ├── server.py               # aiohttp HTTP/SSE server（channel 端）
│   │   ├── agent.py                # 核心 agent loop + history 持久化
│   │   ├── protocol.py             # 协议类型（ToolFunction, ChatCompletionChunk 等）
│   │   ├── tools.py                # 从 tools/*.py 加载 tool 函数
│   │   └── scheduler.py            # cron 调度器
│   └── channel/
│       ├── __init__.py
│       ├── _types.py               # FileChunk, TextChunk, Chunk
│       ├── _core.py                # ChannelCore — 连接管理 + SSE 管道
│       ├── repl/
│       │   ├── __init__.py         # ChannelRepl dataclass + run()
│       │   └── client.py           # thin client — delegate HTTP/SSE to ChannelCore
│       ├── cli/
│       │   ├── __init__.py         # ChannelCli dataclass + run()
│       │   └── client.py           # thin client — delegate HTTP/SSE to ChannelCore
│       └── telegram/
│           ├── __init__.py         # ChannelTelegram dataclass + run()
│           └── client.py           # bot handler + streaming + file send/recv
├── tests/
│   ├── __init__.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_ai_error_handling.py
│   │   ├── test_channel_error.py
│   │   ├── test_channel_repl_cli.py
│   │   ├── test_end_to_end.py
│   │   ├── test_session_concurrency.py
│   │   ├── test_session_tools.py
│   │   └── test_session_workspace.py
│   └── psi_agent/
│       ├── ai/
│       │   ├── test_ai.py
│       ├── session/
│       │   ├── test_protocol.py
│       │   ├── test_agent.py
│       │   ├── test_tools.py
│       │   ├── test_scheduler.py
│       │   └── test_session.py
│       └── channel/
│           ├── test_repl.py
│           └── test_cli.py
├── examples/
│   └── a-simple-bash-only-workspace/
│       ├── tools/
│       │   └── bash.py
│       ├── skills/
│       │   └── hyw/
│       │       └── SKILL.md
│       ├── schedules/
│       │   └── test-task/
│       │       └── TASK.md
│       └── systems/
│           └── system.py
├── .github/
│   ├── workflows/
│   │   └── ci.yml
│   └── dependabot.yml
├── LICENSE.md
```

---

## 4. 架构与数据流

```
Channel (REPL/CLI/Telegram)        Session                     AI (OpenAI/Anthropic)
     │                         │                              │
     │ POST /chat/completions                              │
     │ (不发送 history)         │                              │
     │────────────────────────▶│                              │
     │                         │ 持有锁（后续请求排队等待）    │
     │                         │ 拼上 history + tools         │
     │                         │                              │
     │                         │ POST /chat/completions    │
     │                         │ (streaming + tools)          │
     │                         │─────────────────────────────▶│
     │                         │                              │
     │                         │ SSE chunks (content/        │
     │                         │ reasoning_content/tool_calls)│
     │                         │◀─────────────────────────────│
     │                         │                              │
     │                         │ [若 tool_calls]              │
     │                         │ 解析 → await tool() →       │
     │                         │ 追加结果到 messages →        │
      │                         │ 再次请求 AI（循环，可配置，默认128轮）│
     │                         │─────────────────────────────▶│
     │                         │          ...                 │
     │                         │                              │
     │ SSE chunks (reasoning_content + content)               │
     │◀────────────────────────│                              │
     │                         │ 释放锁                       │
```

**通信协议**：所有 socket 端点使用标准 HTTP/SSE（OpenAI Chat Completions 兼容格式），支持 Unix socket、TCP、Windows Named Pipe。传输类型由地址前缀自动检测：`http(s)://` → TCP，`\\\\.\\pipe\\` → Named Pipe，裸路径 → Unix socket。检测逻辑位于 `psi_agent._socket`。

**错误响应格式**（两种形式）：

1. **非流式（HTTP 层面）**：请求解析失败等，在 `response.prepare()` 之前返回
   ```json
   {"error": {"message": "...", "type": "...", "code": "..."}}
   ```

2. **流式（SSE 层面）**：已 commit HTTP 200 后发生的错误（上游异常、连接断开等），使用 ChatCompletionChunk 格式
   ```json
   {"id": "error", "choices": [{"index": 0, "delta": {"content": "[Upstream Error 401]: ..."}, "finish_reason": "error"}]}
   ```
   所有层统一使用 `finish_reason="error"` 标记流式错误，Session 检测到后不写入 conversation history。

> `finish_reason="error"` 是 psi-agent 的扩展，不在 OpenAI 标准枚举内（标准仅 `stop`/`length`/`tool_calls`/`content_filter`/`function_call`）。仅用于内部层间通信。

---

## 5. AI 层

AI 层是一个统一的多 provider LLM 客户端，对外提供 OpenAI-compatible HTTP/SSE 服务。基于 [any-llm-sdk](https://github.com/mozilla-ai/any-llm) 支持 50+ LLM provider，含 Anthropic→OpenAI SSE 格式自动转换。


- **错误处理**：HTTP 层返回 OpenAI 格式 `{"error": {...}}` JSON；SSE 层使用 ChatCompletionChunk + `finish_reason="error"`
- **`serve_ai()`**：HTTP/SSE 服务器脚手架

### 5.1 Ai

**Dataclass**（`psi_agent/ai/__init__.py`）：

```python
@dataclass
class Ai:
    session_socket: str
    provider: str = ""       # any-llm-sdk provider key（openai, anthropic, gemini, ...）
    model: str = ""          # 模型名
    api_key: str = ""        # 上游 API key
    base_url: str = ""       # 上游 base URL
    verbose: bool = False
    async def run(self) -> None
```

全部参数可选，fallback 到 `PSI_AI_PROVIDER` / `PSI_AI_MODEL` / `PSI_AI_API_KEY` / `PSI_AI_BASE_URL` 环境变量。

### 5.2 Handler（`ai/server.py`）

接收 Session 发来的 body，透传给 `any_llm.acompletion(provider=..., stream=True, ...)`，SSE chunk 通过 `chunk.model_dump_json()` 序列化返回。

### 5.3 支持的 Provider

any-llm-sdk 原生支持的 50+ provider 全部可用，无需额外配置。包括：OpenAI, Anthropic, Gemini, DeepSeek, Mistral, Groq, Ollama, Cerebras, Cohere, Perplexity, Fireworks, Together, xAI, Bedrock, Azure, VertexAI 等。

---

## 6. Session 层

### 6.1 Dataclass

```python
@dataclass
class Session:
    workspace: str
    channel_socket: str
    ai_socket: str
    max_tool_rounds: int = 128
    verbose: bool = False
    session_id: str | None = None

    async def run(self) -> None: ...
```

### 6.2 Workspace 解析

**Tool 加载**（`tools.py`）：
- 遍历 `workspace/tools/*.py`（不含 `_` 开头文件）
- 文件内所有非 `_` 开头的 `async def` 函数均加载为 tool
- 同名 tool 冲突时跳过并记录 warning

**System Prompt 加载**：
- 从 `workspace/systems/system.py` 导入 `system_prompt_builder`
- 调用 `await system_prompt_builder()` 获取 system prompt 字符串
- `system_prompt_builder` 通过 `__file__` 自省自己的路径，自行遍历 `../skills/` 并构建 system prompt

**Schedule 加载**（`scheduler.py`）：
- 遍历 `workspace/schedules/*/TASK.md`
- 解析 YAML front matter（`name`, `cron`）
- 用 `croniter` 解析 cron 表达式
- 启动独立 anyio task（`scheduler.py:run_one_schedule`），每个 schedule 一个 task，各自 sleep + 触发
- 触发时：将 markdown body 作为 user message 发送给 AI
- AI 回复暂存于 session 内部，下次 channel 请求时和新回复一起返回

### 6.3 Agent Loop（`agent.py`）

```
收到 channel 请求时（handle_chat_completions handler）：
  0. 解析 request body → 取最后一条 user message，其余字段透传到 AI
  1. 获取 `anyio.Lock`（FIFO 排队等待）
  2. agent.run(user_message, extra_params=...) 内部：
     a. 惰性构建 system prompt（首次 run，history 尚无 system 消息）
     b. 检查暂存 schedule 响应 → 有则先流式返回
     c. 将 user message 追加到 self.history
     d. 构建请求：history + tools + extra_params → POST ai_socket（streaming）
      e. SSE 流处理（每 chunk 恰好 1 个 choice，多 choice 报错，0 choice 心跳跳过）：
         - content delta          → yield 到 channel + 累计
         - reasoning delta        → yield 到 channel + 累计
         - tool_calls delta       → 累积（按 index 拼接 partial JSON）
         - finish_reason="tool_calls":
             a. 解析完整 tool_calls
             b. 追加 assistant_message(tool_calls) + reasoning_content + tool_result 到 history
             c. 回到步骤 d（最多 max_tool_rounds 轮，可配置，默认 128）
         - finish_reason="stop":
            最终 content + reasoning_content 追加到 history，释放锁
        - finish_reason="error":
           停止处理，错误不写入 history
  3. 释放锁
```

**Schedule 响应处理**：
- 暂存的 schedule 响应和新消息的回复都正常经过 agent loop
- content 和 reasoning_content 各自存在于各自的消息周期中，不会交错

**关于 history**：
- Channel 不发送 history；每次请求只带最新一条 user message
- Session 内部维护 `self.history: list[dict]`，每轮追加
- `finish_reason="stop"` 后将完整 history 持久化到 `workspace/histories/{session_id}.jsonl`
- 一个 session 实例只有一个 history

**多 Channel 并发**：
- 单请求处理，全局 `anyio.Lock`，后续请求 FIFO 排队等待

### 6.4 Tool 定义格式

Session 启动时构建 tools 列表，每次发 AI 请求时附带：

```json
{
  "type": "function",
  "function": {
    "name": "bash",
    "description": "Execute a bash command and return stdout.",
    "parameters": {
      "type": "object",
      "properties": {
        "command": {
          "type": "string",
          "description": "The bash command to execute."
        }
      },
      "required": ["command"]
    }
  }
}
```

### 6.5 最大 Tool Call 轮数

可配置，默认 128 轮（`Session(max_tool_rounds=...)`），防止死循环。

---

## 7. Channel 层

### 7.1 REPL（`channel/repl/`）

**Dataclass**（`psi_agent/channel/repl/__init__.py`）：
```python
@dataclass
class ChannelRepl:
    session_socket: str
    verbose: bool = False

    async def run(self) -> None: ...
```

**行为**：
- 通过 `ChannelCore` 管理到 `session_socket` 的连接（支持 Unix/TCP/Named Pipe）
- ChannelCore 处理 HTTP/SSE 通信，对外暴露 `post([Chunk]) -> AsyncIterator[Chunk]`
- 输入中的 `FileChunk` 转换为 `[RECV:path]` 协议标记，输出中的 `[SEND:path]` 标记产生 `FileChunk`
- 交互式 REPL 循环：
  - 显示 `> ` 提示符，读取用户输入
  - 通过 `core.post([TextChunk(user_input)])` 发送消息并流式接收
  - SSE 内容由 ChannelCore 缓冲合并后 yield `TextChunk`（默认 1s 间隔）
  - Ctrl+D 退出
- 终端通道忽略 FileChunk（落盘由 future Web channel 实现）
- 历史不传递，每次请求只发一条 user message

### 7.2 CLI（`channel/cli/`）

**Dataclass**（`psi_agent/channel/cli/__init__.py`）：
```python
@dataclass
class ChannelCli:
    session_socket: str
    message: str
    verbose: bool = False

    async def run(self) -> None: ...
```

**行为**：
- 通过 `ChannelCore` 管理到 `session_socket` 的连接
- `core.post([TextChunk(message)])` 发送消息并流式接收
- ChannelCore 处理 HTTP/SSE 和缓冲合并，客户端仅处理 `TextChunk`
- 显示完毕后退出

---

## 8. CLI 结构（tyro）

`psi_agent/cli.py` 作为唯一入口：

```python
from typing import Annotated
from tyro import conf

from psi_agent._run import Run
from psi_agent.ai import Ai
from psi_agent.session import Session
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.telegram import ChannelTelegram

ChannelGroup = Annotated[
    ChannelRepl | ChannelCli | ChannelTelegram,
    conf.subcommand(name="channel", description="User interface channels"),
]

def main() -> None:
    cmd = tyro.cli(Run | Ai | Session | ChannelGroup)
    anyio.run(cmd.run)
```

生成的 CLI 结构：
```
psi-agent session --workspace ... --channel-socket ... --ai-socket ...
psi-agent ai --provider openai --session-socket ... --model ... --api-key ... --base-url ...
psi-agent ai --provider anthropic --session-socket ... --model ... --api-key ... --base-url ...
psi-agent channel repl --session-socket ...
psi-agent channel cli --session-socket ... --message ...
psi-agent channel telegram --session-socket ... --bot-token ...
psi-agent run config.yml
```

全局共用 `--verbose` 参数开启 DEBUG 日志。

---

## 9. 日志规范

**配置**（`psi_agent/_logging.py`）：
```python
from loguru import logger

def setup_logging(*, verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
    )
```

**必须打日志的点**（非详尽）：

| 模块 | 场景 | 级别 |
|------|------|------|
| 全局 | 组件启动/停止、socket 绑定、参数 | INFO |
| AI | 请求到达、上游请求发出、上游响应状态码 | INFO |
| AI | 每个 SSE chunk 内容 | DEBUG |
| AI | Anthropic→OpenAI thinking 转换前后 | DEBUG |
| Session | 锁获取/释放 | DEBUG |
| Session | History 追加（内容摘要） | DEBUG |
| Session | Tool 加载（每个 tool 名称和参数） | INFO |
| Session | Tool 调用（函数名、参数、返回值摘要） | INFO |
| Session | Schedule 触发、AI 请求发送 | INFO |
| Session | History 加载/保存 | INFO/DEBUG |
| Session | 错误（繁忙、AI socket 不可达等） | ERROR |
| Channel | Socket 连接/断开 | INFO |
| Channel | 消息发送/接收 | DEBUG |

---

## 10. 动态版本号

`pyproject.toml` 配置：
```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "psi-agent"
dynamic = ["version"]

[tool.hatch.version]
source = "vcs"
```

`uv build` 时自动从 `git describe --tags` 获取版本。初始 tag 建议 `v0.1.0`。

---

## 11. 测试策略

- **框架**：`pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`，anyio backend）
- **夹具**（`conftest.py`）：
  - `temp_workspace`：创建临时 workspace 目录（含示例 tools、system.py）
  - `mock_ai_socket`：启动 aiohttp test server 模拟 AI socket
  - `session_socket_path`：生成临时 socket 路径
- **测试范围**：
  - AI 层：验证格式转换正确性、SSE 流透传
  - Session 层：tool 加载、agent loop 逻辑、并发锁、schedule 触发
  - Channel 层：消息发送、SSE 解析、显示逻辑

---

## 12. Workspace 示例规格

`examples/a-simple-bash-only-workspace/` 提供最小可运行示例。

### `tools/bash.py`
```python
"""Execute bash commands."""
import anyio

async def bash(command: str) -> str:
    """Execute a bash command and return stdout.

    Args:
        command: The bash command to execute.
    """
    result = await anyio.run_process(["/bin/bash", "-c", command])
    return result.stdout.decode().strip()
```

### `systems/system.py`
```python
"""Build the system prompt for the bash-only agent."""
import inspect
from pathlib import Path
from psi_agent._yaml import parse_yaml_header

async def system_prompt_builder() -> str:
    current_file = Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"

    skills = []
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            header, _ = parse_yaml_header(skill_md.read_text())
            if header and header.get("name") and header.get("description"):
                skills.append(f"- {header['name']}: {header['description']}")

    skills_text = "\n".join(skills) if skills else "(None)"

    return f"""You are a helpful AI assistant.

## Workspace
Location: {workspace_root}

## Skills
Location: {skills_dir}

Available:
{skills_text}"""
```

### `schedules/test-task/TASK.md`
```yaml
---
name: daily-report
cron: "0 12 * * *"
---
请生成一份项目进展日报。
```

---

## 13. 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-05-20 | v0.1.0-draft | 初始设计规格：微内核架构、三组件 Unix socket 通信、OpenAI/Anthropic AI 后端、REPL+CLI Channel、Workspace 驱动 |
| 2026-05-21 | v0.1.1 | 技术栈补充：prompt-toolkit 异步 REPL；集成测试计划扩展（15 个 corner case 类别，40+ 测试场景） |
| 2026-05-22 | v0.1.2 | 架构调整：src-layout、Rich Console 替代 print()、per-file-ignore 清零、ruff 规则扩展（B/RUF/N/T20/PLC）、2 处 ty:ignore 定型 |
| 2026-05-23 | v0.2.0 | 并发模型重构（FIFO 排队）、调度器重构（每 schedule 独立 task）、统一 SSE 流错误格式、去重 _yaml.py、CLI 参数全支持环境变量（model/base_url/api_key）、错误不污染 history、137 测试全绿 |
| 2026-05-24 | v0.2.1 | 内部模块规范化：`logging.py` → `_logging.py`、`protocol.py` → `_protocol.py` |
| 2026-05-24 | v0.2.3 | AI 层抽象：`SSEChunk` dataclass 替代裸 dict 构造 + `serve_ai_backend()` 消除 serve 重复 |
| 2026-06-17 | v0.3.0 | 统一 AI 后端：采用 any-llm-sdk 替代手写 Anthropic→OpenAI 转换，单一 `Ai` 支持 50+ provider |
| 2026-06-24 | v0.4.0 | Session 全面重构：协议类型内联、tool 加载通用化、schedule 纯配置化、参数透传、单 choice 强制、history JSONL 持久化、Interleaved CoT 支持、socket 传输统一（Unix/TCP/Named Pipe） |
| 2026-06-25 | v0.5.0 | Channel 重构 + Telegram：ChannelCore 公共部件提取（Chunk 类型、SSE 缓冲合并、SEND/RECV 协议标记）、CLI/REPL 瘦身、Telegram bot channel（流式 edit_text + 文件收发 + SOCKS5 proxy + 用户白名单） |
