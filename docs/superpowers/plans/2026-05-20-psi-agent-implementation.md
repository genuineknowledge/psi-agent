# psi-agent 微内核 Agent 框架 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 psi-agent 微内核 agent 框架，包含 ai/session/channel 三个独立组件，通过 Unix socket 以 OpenAI HTTP/SSE 协议通信。

**Architecture:** 单 Python package（`psi_agent/`），tyro Union dataclasses 驱动 CLI 子命令。所有 IO 异步（anyio）。组件通过 aiohttp Unix socket 通信。Session 解析 workspace 目录结构并按需执行 tool。

**Tech Stack:** Python >= 3.14, anyio, aiohttp, tyro, loguru, ruff, hatch-vcs, pytest + pytest-asyncio(anyio mode)

**Design Spec:** `docs/superpowers/specs/2026-05-20-psi-agent-design.md`

---

### Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `psi_agent/__init__.py`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "psi-agent"
dynamic = ["version"]
description = "A microkernel-style agent framework"
requires-python = ">=3.14"
dependencies = [
    "anyio>=4.0",
    "aiohttp>=3.9",
    "loguru>=0.7",
    "tyro>=0.9",
    "croniter>=6.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.11",
]

[project.scripts]
psi-agent = "psi_agent.cli:main"

[tool.hatch.version]
source = "vcs"

[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "ASYNC", "SIM", "C4"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

- [ ] **Step 2: 创建 .gitignore**

```
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
dist/
.ruff_cache/
.pytest_cache/
*.sock
```

- [ ] **Step 3: 创建 psi_agent/__init__.py**

```python
"""psi-agent: A microkernel-style agent framework."""
```

- [ ] **Step 4: 创建测试目录结构，添加 conftest.py**

```bash
mkdir -p tests/psi_agent/ai tests/psi_agent/session tests/psi_agent/channel
```

```python
# tests/conftest.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 5: 安装依赖并设置初始 git tag**

```bash
uv sync
git add -A && git commit -m "chore: project scaffold"
git tag v0.1.0
uv build
```

Expected: `uv build` 成功生成 `dist/psi_agent-0.1.0.tar.gz`

---

### Task 2: 日志模块

**Files:**
- Create: `psi_agent/logging.py`
- Create: `tests/psi_agent/test_logging.py`

- [ ] **Step 1: 写测试**

```python
# tests/psi_agent/test_logging.py
from loguru import logger

from psi_agent.logging import setup_logging


def test_setup_logging_default_info():
    logger.remove()
    handler_id = setup_logging(verbose=False)
    assert handler_id is not None
    logger.remove(handler_id)


def test_setup_logging_verbose_debug():
    logger.remove()
    handler_id = setup_logging(verbose=True)
    assert handler_id is not None
    logger.remove(handler_id)
```

- [ ] **Step 2: 实现 logging.py**

```python
# psi_agent/logging.py
import sys

from loguru import logger


def setup_logging(*, verbose: bool = False) -> int:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    handler_id = logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    return handler_id
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
uv run pytest tests/psi_agent/test_logging.py -v
git add psi_agent/logging.py tests/psi_agent/test_logging.py && git commit -m "feat: add loguru-based logging setup"
```

---

### Task 3: OpenAI 协议类型

**Files:** `psi_agent/protocol.py`, `tests/psi_agent/test_protocol.py`

实现 `Message`, `ToolFunction`, `ToolDef`, `ChatCompletionRequest`, `DeltaMessage`, `StreamChoice`, `ChatCompletionChunk`, `ErrorResponse` 等 dataclass。核心功能：
- `ToolFunction.from_callable()` 从 async 函数 inspect 出 name/description/parameters
- `ChatCompletionRequest.to_json()` / `from_dict()` 序列化
- `ChatCompletionChunk.to_sse()` 生成 SSE 格式字符串
- `ErrorResponse.to_json()` 生成 OpenAI 兼容错误响应

具体代码参考 spec 文档第 5 节。TDD：先写测试覆盖序列化/SSE生成/from_callable，再实现。

---

### Task 4: AI 层 — openai-completions

**Files:** `psi_agent/ai/__init__.py`, `psi_agent/ai/openai_completions/__init__.py`, `psi_agent/ai/openai_completions/server.py`, `tests/psi_agent/ai/test_openai_completions.py`

**Dataclass:** `AiOpenAICompletions(session_socket, model, api_key, base_url, verbose)` with `async run()`

**Server:** aiohttp Unix socket HTTP server，接收 `POST /v1/chat/completions`，透传到上游（设置 Authorization header + model），SSE 流式返回。每个 chunk 打 DEBUG 日志。

**测试:** mock 上游 aiohttp server，验证透传正确性。

---

### Task 5: AI 层 — anthropic-messages

**Files:** `psi_agent/ai/anthropic_messages/__init__.py`, `psi_agent/ai/anthropic_messages/server.py`, `tests/psi_agent/ai/test_anthropic_messages.py`

**核心逻辑:**
- `_convert_openai_messages_to_anthropic()`: OpenAI messages → Anthropic messages（含 system/tool/tool_result 转换）
- `_convert_openai_tools_to_anthropic()`: OpenAI tool defs → Anthropic tool format
- `_convert_anthropic_stream_to_openai_sse()`: Anthropic SSE events → OpenAI SSE chunks
  - `thinking_delta` → `reasoning_content`
  - `text_delta` → `content`
  - `input_json_delta` → `tool_calls`

---

### Task 6: Session — Tool 加载

**Files:** `psi_agent/session/__init__.py`, `psi_agent/session/tools.py`, `tests/psi_agent/session/test_tools.py`

`load_tools_from_workspace(tools_dir: Path) -> dict[str, ToolFunction]`:
1. 遍历 `tools_dir/*.py`
2. 对每个 `.py` 文件，用 `importlib` 动态加载模块
3. 找到与文件名同名的 `async def` 函数
4. 用 `ToolFunction.from_callable()` 构建 OpenAI tool 定义
5. 跳过非 async、私有（`_` 开头）函数

---

### Task 7: Session — Scheduler

**Files:** `psi_agent/session/scheduler.py`, `tests/psi_agent/session/test_scheduler.py`

`load_schedules_from_workspace(schedules_dir: Path) -> list[Schedule]`:
1. 遍历 `schedules/*/TASK.md`
2. 解析 YAML front matter（`name`, `cron`）
3. 用 `croniter` 解析 cron 表达式
4. `Schedule.to_user_message()` 生成带标注的 user message

`ScheduleRunner` 后台 anyio task 定期检查触发。

---

### Task 8: Session — Agent Loop

**Files:** `psi_agent/session/agent.py`, `tests/psi_agent/session/test_agent.py`

`SessionAgent` 类：
- `self.history: list[dict]` — 单一 history
- `self._lock: anyio.Lock` — 串行锁
- `self._tool_funcs: dict[str, callable]` — 已注册的实际 tool 函数
- `self._pending_schedule_response: Optional[list[ChatCompletionChunk]]` — 暂存的 schedule 响应

`async run(user_message: dict) -> AsyncIterator[ChatCompletionChunk]`:
1. 如果有 `_pending_schedule_response`，先 yield 所有暂存 chunk，清空暂存
2. 将 user_message 追加到 history
3. 构建 OpenAI 请求（history + tools），POST ai_socket，流式读取
4. 积累 tool_calls fragments
5. 收到 `finish_reason="stop"` → 结束
6. 收到 `finish_reason="tool_calls"` → 执行 tool（用非 streaming 请求获取完整 tool_calls 消息），将结果加到 history，yield reasoning_content chunks，回到步骤 3
7. 最多 10 轮 tool call

---

### Task 9: Session — Channel-facing HTTP Server

**Files:** `psi_agent/session/server.py`, `psi_agent/session/__init__.py` (更新)

aiohttp Unix socket server 监听 `channel_socket`:
- `POST /v1/chat/completions`: 解析请求，用 `SessionAgent.run()` 处理，SSE 流式返回
- 用 `anyio.Lock` 确保单请求；忙时返回 503 + error JSON
- 请求 body 中的 messages 只取最后一条 user 消息

`SessionConfig.run()`: 加载 workspace（tools, system_prompt, schedules），启动后台 schedule runner，启动 channel server。

---

### Task 10: Channel — REPL

**Files:** `psi_agent/channel/__init__.py`, `psi_agent/channel/repl/__init__.py`, `psi_agent/channel/repl/client.py`, `tests/psi_agent/channel/test_repl.py`

`ChannelRepl.run()`:
1. 连接 `session_socket` Unix socket
2. 交互式循环：显示 `> `，读取行
3. `POST /v1/chat/completions`（不发送 history）
4. SSE 流式读取，实时打印 reasoning_content（dimmed）和 content
5. Ctrl+C/D 退出

---

### Task 11: Channel — CLI

**Files:** `psi_agent/channel/cli/__init__.py`, `psi_agent/channel/cli/client.py`, `tests/psi_agent/channel/test_cli.py`

`ChannelCli.run()`:
1. 连接 `session_socket`，发送 `--message`
2. SSE 流式读取，显示结果后退出

---

### Task 12: CLI 入口

**Files:** `psi_agent/cli.py`

```python
from tyro import conf
from typing import Annotated

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
    import anyio
    cmd = tyro.cli(SessionConfig | AiGroup | ChannelGroup)
    anyio.run(cmd.run)
```

---

### Task 13: 示例 Workspace + README + AGENTS.md

**Files:**
- `examples/a-simple-bash-only-workspace/tools/bash.py`
- `examples/a-simple-bash-only-workspace/skills/bash-expert/SKILL.md`
- `examples/a-simple-bash-only-workspace/schedules/daily-report/TASK.md`
- `examples/a-simple-bash-only-workspace/systems/system.py`
- `README.md`（中文）
- `AGENTS.md`（中文）

---

### Task 14: 集成测试 + Ruff + Ty 检查

- [ ] 端到端集成测试：启动 AI server → 启动 session → CLI channel 发送消息 → 验证流式响应
- [ ] `uv run ruff check .` 无错误
- [ ] `uv run ruff format --check .` 无差异
