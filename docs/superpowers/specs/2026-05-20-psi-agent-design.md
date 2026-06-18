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
│   ├── _logging.py                  # loguru 配置
│   ├── ai/  (统一多 provider，基于 any-llm-sdk)
│   │   ├── __init__.py
│   │       └── server.py           # aiohttp Unix socket server + thinking 转换
│   ├── session/
│   │   ├── __init__.py             # Session dataclass + run()
│   │   ├── server.py               # aiohttp Unix socket server（channel 端）
│   │   ├── agent.py                # 核心 agent loop
│   │   ├── tools.py                # 从 tools/*.py 加载 tool 函数
│   │   └── scheduler.py            # cron 调度器
│   └── channel/
│       ├── __init__.py
│       ├── repl/
│       │   ├── __init__.py         # ChannelRepl dataclass + run()
│       │   └── client.py           # 连接 session socket，REPL 循环（Rich + prompt_toolkit multiline）
│       └── cli/
│           ├── __init__.py         # ChannelCli dataclass + run()
│           └── client.py           # 连接 session socket（Rich 格式化输出）
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
│       │   ├── test_ai_backend.py
│       ├── session/
│       │   ├── test_agent.py
│       │   ├── test_tools.py
│       │   └── test_scheduler.py
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
Channel (REPL/CLI)          Session                     AI (OpenAI/Anthropic)
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
     │                         │ 再次请求 AI（循环，最多10轮）│
     │                         │─────────────────────────────▶│
     │                         │          ...                 │
     │                         │                              │
     │ SSE chunks (reasoning_content + content)               │
     │◀────────────────────────│                              │
     │                         │ 释放锁                       │
```

**通信协议**：所有 socket 端点使用标准 HTTP/SSE（OpenAI Chat Completions 兼容格式），通过 aiohttp 的 Unix socket 支持。

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

AI 层是一个统一的多 provider LLM 客户端，通过 Unix socket 对外提供 OpenAI-compatible HTTP/SSE 服务。基于 [any-llm-sdk](https://github.com/mozilla-ai/any-llm) 支持 50+ LLM provider，含 Anthropic→OpenAI SSE 格式自动转换。


- **错误处理**：HTTP 层返回 OpenAI 格式 `{"error": {...}}` JSON；SSE 层使用 ChatCompletionChunk + `finish_reason="error"`
- **`serve_ai_backend()`**：Unix socket 服务器脚手架

### 5.1 AiBackend

**Dataclass**（`psi_agent/ai/__init__.py`）：

```python
@dataclass
class AiBackend:
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
    model: str = "gpt-4"
    verbose: bool = False

    async def run(self) -> None: ...
```

### 6.2 Workspace 解析

**Tool 加载**（`tools.py` + `session/__init__.py`）：
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
- 启动独立 anyio task，每个 schedule 一个 task，各自 sleep + 触发
- 触发时：将 markdown body 作为 user message 加上标注（"[Schedule Task: {name}]"）发送给 AI
- AI 回复暂存于 session 内部，下次 channel 请求时和新回复一起返回

### 6.3 Agent Loop（`agent.py`）

```
收到 channel 请求时：
  0. 检查暂存 schedule 响应 → 有则先流式返回（reasoning_content + content）
  1. 获取 `anyio.Lock`（FIFO 排队等待）
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
      - finish_reason="error":
          停止处理，错误不写入 history
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
  - `POST /chat/completions` 发送消息（不含 history）
  - SSE 流式接收，实时显示：
    - `reasoning_content`：dimmed/灰色显示（标注 `[思考]`）
    - `content`：正常显示
  - Ctrl+D 退出
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
- `POST /chat/completions` 发送 `message`
- SSE 流式接收，实时显示 reasoning_content + content
- 显示完毕后退出

---

## 8. CLI 结构（tyro）

`psi_agent/cli.py` 作为唯一入口：

```python
from typing import Annotated
from tyro import conf

from psi_agent.ai import AiBackend
from psi_agent.session import Session
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.cli import ChannelCli

AiGroup = Annotated[
    AiBackend,
    conf.subcommand(name="ai", description="AI backend services"),
]

ChannelGroup = Annotated[
    ChannelRepl | ChannelCli,
    conf.subcommand(name="channel", description="User interface channels"),
]

def main() -> None:
    cmd = tyro.cli(Session | AiGroup | ChannelGroup)
    anyio.run(cmd.run)
```

生成的 CLI 结构：
```
psi-agent session --workspace ... --channel-socket ... --ai-socket ...
psi-agent ai --provider openai --session-socket ... --model ... --api-key ... --base-url ...
psi-agent ai --provider anthropic --session-socket ... --model ... --api-key ... --base-url ...
psi-agent channel repl --session-socket ...
psi-agent channel cli --session-socket ... --message ...
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
| 2026-06-17 | v0.3.0 | 统一 AI 后端：采用 any-llm-sdk 替代手写 Anthropic→OpenAI 转换，单一 `AiBackend` 支持 50+ provider |
