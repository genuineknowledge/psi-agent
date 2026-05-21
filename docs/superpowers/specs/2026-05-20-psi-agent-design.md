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
├── psi_agent/
│   ├── __init__.py
│   ├── cli.py                      # tyro CLI 入口
│   ├── logging.py                  # loguru 配置
│   ├── protocol.py                 # OpenAI 兼容协议类型
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── openai_completions/
│   │   │   ├── __init__.py         # AiOpenAICompletions dataclass + run()
│   │   │   └── server.py           # aiohttp Unix socket server
│   │   └── anthropic_messages/
│   │       ├── __init__.py         # AiAnthropicMessages dataclass + run()
│   │       └── server.py           # aiohttp Unix socket server + thinking 转换
│   ├── session/
│   │   ├── __init__.py             # SessionConfig dataclass + run()
│   │   ├── server.py               # aiohttp Unix socket server（channel 端）
│   │   ├── agent.py                # 核心 agent loop
│   │   ├── workspace.py            # 解析 workspace 结构
│   │   ├── tools.py                # 从 tools/*.py 加载 tool 函数
│   │   └── scheduler.py            # cron 调度器
│   └── channel/
│       ├── __init__.py
│       ├── repl/
│       │   ├── __init__.py         # ChannelRepl dataclass + run()
│       │   └── client.py           # 连接 session socket，REPL 循环
│       └── cli/
│           ├── __init__.py         # ChannelCli dataclass + run()
│           └── client.py           # 连接 session socket，单次消息
├── examples/
│   └── a-simple-bash-only-workspace/
│       ├── tools/
│       │   └── bash.py
│       ├── skills/
│       │   └── bash-expert/
│       │       └── SKILL.md
│       ├── schedules/
│       │   └── daily-report/
│       │       └── TASK.md
│       └── systems/
│           └── system.py
└── tests/
    ├── conftest.py
    └── psi_agent/
        ├── ai/
        │   ├── test_openai_completions.py
        │   └── test_anthropic_messages.py
        ├── session/
        │   ├── test_agent.py
        │   ├── test_workspace.py
        │   ├── test_tools.py
        │   └── test_scheduler.py
        └── channel/
            ├── test_repl.py
            └── test_cli.py
```

---

## 4. 架构与数据流

```
Channel (REPL/CLI)          Session                     AI (OpenAI/Anthropic)
     │                         │                              │
     │ POST /v1/chat/completions                              │
     │ (不发送 history)         │                              │
     │────────────────────────▶│                              │
     │                         │ 持有锁（忙→503 error JSON）   │
     │                         │ 拼上 history + tools         │
     │                         │                              │
     │                         │ POST /v1/chat/completions    │
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
     │                         │ 再次请求 AI（循环，最多10轮）│
     │                         │─────────────────────────────▶│
     │                         │          ...                 │
     │                         │                              │
     │ SSE chunks (reasoning_content + content)               │
     │◀────────────────────────│                              │
     │                         │ 释放锁                       │
```

**通信协议**：所有 socket 端点使用标准 HTTP/SSE（OpenAI Chat Completions 兼容格式），通过 aiohttp 的 Unix socket 支持。

**错误响应格式**（统一）：
```json
{
  "error": {
    "message": "Session is currently processing another request",
    "type": "session_busy",
    "code": "busy"
  }
}
```

---

## 5. AI 层

### 5.1 openai-completions

**Dataclass**（定义在 `psi_agent/ai/openai_completions/__init__.py`）：
```python
@dataclass
class AiOpenAICompletions:
    session_socket: str
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    verbose: bool = False

    async def run(self) -> None: ...
```

**行为**：
- 在 `session_socket` 上启动 aiohttp Unix socket HTTP server
- 接收 OpenAI-compatible `POST /v1/chat/completions` 请求
- 转发到 `base_url`（设置 `model` + `api_key` 的 Authorization header）
- 流式 SSE 透传
- 每个请求和 chunk 打 DEBUG 日志

### 5.2 anthropic-messages

**Dataclass**（定义在 `psi_agent/ai/anthropic_messages/__init__.py`）：
```python
@dataclass
class AiAnthropicMessages:
    session_socket: str
    model: str
    api_key: str
    base_url: str
    verbose: bool = False

    async def run(self) -> None: ...
```

**行为**：
- 接收 OpenAI-compatible `POST /v1/chat/completions` 请求
- 将 OpenAI 格式的 messages 和 tools 转换为 Anthropic Messages API 格式
- 转发到 `base_url`（x-api-key header + anthropic-version header）
- 将 Anthropic 响应流转换为 OpenAI SSE 格式：
  - Anthropic `thinking` block → OpenAI `reasoning_content`
  - Anthropic `text` block → OpenAI `content`
  - Anthropic `tool_use` block → OpenAI `tool_calls`
- 每个 chunk 打 DEBUG 日志

---

## 6. Session 层

### 6.1 Dataclass

```python
@dataclass
class SessionConfig:
    workspace: str
    channel_socket: str
    ai_socket: str
    verbose: bool = False

    async def run(self) -> None: ...
```

### 6.2 Workspace 解析（`workspace.py`）

**Tool 加载**（`tools.py`）：
- 遍历 `workspace/tools/*.py`
- 对每个 `.py` 文件，找到**与文件名同名**的函数（例如 `bash.py` → `bash` 函数）
- 用 `inspect.signature()` 解析参数（类型注解、默认值）
- 用 `inspect.getdoc()` 提取 docstring
- 构建 OpenAI function tool definition
- Tool 函数必须是 `async def`

**System Prompt 加载**：
- 从 `workspace/systems/system.py` 导入 `system_prompt_builder`
- 调用 `await system_prompt_builder()` 获取 system prompt 字符串
- `system_prompt_builder` 通过 `__file__` 自省自己的路径，自行遍历 `../skills/` 并构建 system prompt

**Schedule 加载**（`scheduler.py`）：
- 遍历 `workspace/schedules/*/TASK.md`
- 解析 YAML front matter（`name`, `cron`）
- 用 `croniter` 解析 cron 表达式
- 启动后台 anyio task，轮询检查触发时机
- 触发时：将 markdown body 作为 user message 加上标注（"[Schedule Task: {name}]"）发送给 AI
- AI 回复暂存于 session 内部，下次 channel 请求时和新回复一起返回

### 6.3 Agent Loop（`agent.py`）

```
收到 channel 请求时：
  0. 检查暂存 schedule 响应 → 有则先流式返回（reasoning_content + content）
  1. 持有锁（忙则返回 503 error JSON）
  2. 将 user message 追加到 self.history
  3. 构建请求：model + history + tools → POST ai_socket
  4. SSE 流处理循环：
     - 收到 content delta     → 作为 StreamChunk（content），发送到 channel
     - 收到 reasoning delta   → 作为 StreamChunk（reasoning_content），发送到 channel
     - 收到 tool_calls delta  → 累积
     - finish_reason="tool_calls":
         a. 解析完整 tool_calls
         b. 在已注册 tools 中查找匹配的函数
         c. await tool(**args)，得到结果
         d. 将 assistant_message(tool_calls) + tool_result 追加到 history
         e. 将 tool 调用意图和结果包装为 reasoning_content chunk，发送到 channel
         f. 继续第 3 步（最多循环 10 轮）
     - finish_reason="stop":
         最终 content 流式完成
  5. 释放锁
```

**Schedule 响应处理**：
- 暂存的 schedule 响应和新消息的回复都正常经过 agent loop
- content 和 reasoning_content 各自存在于各自的消息周期中，不会交错

**关于 history**：
- Channel 不发送 history；每次请求只带最新一条 user message
- Session 内部维护 `self.history: list[dict]`，每轮追加
- History 仅存在于内存，不持久化
- 一个 session 实例只有一个 history

**多 Channel 并发**：
- 单请求处理，全局 `anyio.Lock`
- 忙时返回 `503` + error JSON

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

限制为 10 轮，防止死循环。

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
- 连接 `session_socket`（aiohttp Unix socket 客户端）
- 交互式 REPL 循环：
  - 显示 `> ` 提示符，读取用户输入
  - `POST /v1/chat/completions` 发送消息（不含 history）
  - SSE 流式接收，实时显示：
    - `reasoning_content`：dimmed/灰色显示（标注 `[思考]`）
    - `content`：正常显示
  - Ctrl+C / Ctrl+D 退出
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
- 连接 `session_socket`
- `POST /v1/chat/completions` 发送 `message`
- SSE 流式接收，实时显示 reasoning_content + content
- 显示完毕后退出

---

## 8. CLI 结构（tyro）

`psi_agent/cli.py` 作为唯一入口：

```python
from typing import Annotated
from tyro import conf

from psi_agent.ai.openai_completions import AiOpenAICompletions
from psi_agent.ai.anthropic_messages import AiAnthropicMessages
from psi_agent.session import SessionConfig
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.cli import ChannelCli

AiGroup = Annotated[
    AiOpenAICompletions | AiAnthropicMessages,
    conf.subcommand(name="ai", description="AI backend services"),
]

ChannelGroup = Annotated[
    ChannelRepl | ChannelCli,
    conf.subcommand(name="channel", description="User interface channels"),
]

def main() -> None:
    cmd = tyro.cli(SessionConfig | AiGroup | ChannelGroup)
    anyio.run(cmd.run)
```

生成的 CLI 结构：
```
psi-agent session --workspace ... --channel-socket ... --ai-socket ...
psi-agent ai openai-completions --session-socket ... --model ... --api-key ... --base-url ...
psi-agent ai anthropic-messages --session-socket ... --model ... --api-key ... --base-url ...
psi-agent channel repl --session-socket ...
psi-agent channel cli --session-socket ... --message ...
```

全局共用 `--verbose` 参数开启 DEBUG 日志。

---

## 9. 日志规范

**配置**（`psi_agent/logging.py`）：
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
    result = await anyio.run_process(command, shell=True)
    return result.stdout.decode().strip()
```

### `systems/system.py`
```python
"""Build the system prompt for the bash-only agent."""
import inspect
from pathlib import Path

async def system_prompt_builder() -> str:
    skills_dir = Path(inspect.getfile(system_prompt_builder)).parent.parent / "skills"
    skills = []
    for skill_dir in skills_dir.iterdir():
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                skills.append(skill_md.read_text())

    workspace_root = Path(inspect.getfile(system_prompt_builder)).parent.parent

    return f"""You are a helpful assistant with bash access.
Workspace location: {workspace_root}

Available skills:
{chr(10).join(skills)}

Use the bash tool when you need to execute commands."""
```

### `schedules/daily-report/TASK.md`
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
| 2026-05-20 | v0.1.0-draft | 初始设计规格 |
