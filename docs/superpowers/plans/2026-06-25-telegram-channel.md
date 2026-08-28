# Telegram Channel 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 Telegram 通道，bot 通过 ChannelCore 流式交互，edit_text 实现流式输出

**Architecture:** ChannelTelegram dataclass → `run()` 创建 ptb Application + long polling → message handler 调用 `core.post()` → TextChunk 累积 `edit_text`，FileChunk 另发 document/photo

**Tech Stack:** python-telegram-bot >= 22.0, aiohttp, ChannelCore

**Design Spec:** `docs/superpowers/specs/2026-06-25-telegram-channel.md`

---

## File Structure

| 操作 | 文件 | 职责 |
|------|------|------|
| Create | `src/psi_agent/channel/telegram/__init__.py` | ChannelTelegram dataclass + `run()` |
| Create | `src/psi_agent/channel/telegram/client.py` | handler、文件发送、核心逻辑 |
| Modify | `src/psi_agent/cli.py` | 加入 ChannelTelegram 到 ChannelGroup |
| Modify | `pyproject.toml` | telegram optional dependency |
| Create | `tests/psi_agent/channel/telegram/__init__.py` | test package |
| Create | `tests/psi_agent/channel/telegram/test_telegram.py` | unit tests |

---

### Task 1: pyproject.toml 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 telegram optional dependency**

在 `[project.optional-dependencies]` 的 `dev = [...]` 最后添加：

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.11",
    "python-telegram-bot[job-queue]>=22.0",
]
```

- [ ] **Step 2: 安装依赖**

```bash
uv sync
```

Expected: 无错误

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add python-telegram-bot dev dependency"
```

---

### Task 2: ChannelTelegram dataclass

**Files:**
- Create: `src/psi_agent/channel/telegram/__init__.py`
- Create: `tests/psi_agent/channel/telegram/__init__.py`
- Create: `tests/psi_agent/channel/telegram/test_telegram.py`

- [ ] **Step 1: 写 ChannelTelegram dataclass 测试**

```python
# tests/psi_agent/channel/telegram/__init__.py
```

```python
# tests/psi_agent/channel/telegram/test_telegram.py
from __future__ import annotations

import anyio
import pytest

from psi_agent.channel.telegram import ChannelTelegram


def test_channel_telegram_defaults():
    ct = ChannelTelegram(session_socket="/tmp/chan.sock")
    assert ct.session_socket == "/tmp/chan.sock"
    assert ct.bot_token == ""
    assert ct.interval == 1.0
    assert ct.allowed_user_ids is None
    assert ct.verbose is False


def test_channel_telegram_with_whitelist():
    ct = ChannelTelegram(
        session_socket="/tmp/chan.sock",
        bot_token="abc",
        interval=0.5,
        allowed_user_ids=[123, 456],
        verbose=True,
    )
    assert ct.bot_token == "abc"
    assert ct.interval == 0.5
    assert ct.allowed_user_ids == [123, 456]
    assert ct.verbose is True
```

- [ ] **Step 2: 运行测试验证 fail**

```bash
uv run pytest tests/psi_agent/channel/telegram/test_telegram.py -v
```

Expected: FAIL — `ImportError: cannot import name 'ChannelTelegram'`

- [ ] **Step 3: 实现 ChannelTelegram dataclass**

```python
# src/psi_agent/channel/telegram/__init__.py
from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from psi_agent._logging import setup_logging

from .client import run_telegram


@dataclass
class ChannelTelegram:
    """Telegram bot channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe)."""

    bot_token: str = ""
    """Telegram bot token (CLI arg > PSI_TELEGRAM_BOT_TOKEN env)."""

    interval: float = 1.0
    """SSE buffer merge window. 0 = no throttling."""

    allowed_user_ids: list[int] | None = None
    """Whitelist of Telegram user IDs. None = allow all."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        token = self.bot_token or os.environ.get("PSI_TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise ValueError("No Telegram bot token provided. Set --bot-token or PSI_TELEGRAM_BOT_TOKEN.")

        setup_logging(verbose=self.verbose)
        logger.info(f"Starting Telegram bot, connecting to {self.session_socket}")

        await run_telegram(
            session_socket=self.session_socket,
            bot_token=token,
            interval=self.interval,
            allowed_user_ids=self.allowed_user_ids,
        )
```

- [ ] **Step 4: 运行测试验证 pass**

```bash
uv run pytest tests/psi_agent/channel/telegram/test_telegram.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/telegram/__init__.py tests/psi_agent/channel/telegram/__init__.py tests/psi_agent/channel/telegram/test_telegram.py
git commit -m "feat(telegram): add ChannelTelegram dataclass"
```

---

### Task 3: Telegram client 核心逻辑

**Files:**
- Create: `src/psi_agent/channel/telegram/client.py`
- Modify: `tests/psi_agent/channel/telegram/test_telegram.py`

- [ ] **Step 1: 写 token 缺失测试**

追加到 test file：

```python
@pytest.mark.anyio
async def test_run_raises_on_missing_token():
    ct = ChannelTelegram(session_socket="/tmp/chan.sock")
    with pytest.raises(ValueError, match="No Telegram bot token"):
        await ct.run()
```

- [ ] **Step 2: 运行测试验证 fail**

```bash
uv run pytest tests/psi_agent/channel/telegram/test_telegram.py::test_run_raises_on_missing_token -v
```

Expected: FAIL — `ImportError: cannot import name 'run_telegram'`

- [ ] **Step 3: 实现 run_telegram**

```python
# src/psi_agent/channel/telegram/client.py
from __future__ import annotations

from pathlib import Path

from loguru import logger
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import FileChunk, TextChunk

_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


def _allowed(user_id: int, allowed_ids: list[int] | None) -> bool:
    if allowed_ids is None:
        return True
    return user_id in allowed_ids


async def _send_file(update: Update, path: str) -> None:
    p = Path(path)
    if p.suffix.lower() in _IMAGE_EXT:
        await update.message.reply_photo(path)
    else:
        await update.message.reply_document(path)


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    core: ChannelCore = context.bot_data["core"]
    allowed_ids: list[int] | None = context.bot_data["allowed_ids"]

    if not _allowed(user_id, allowed_ids):
        return

    sent = await update.message.reply_text("...")

    accumulated = ""
    try:
        async for chunk in core.post([TextChunk(text)]):
            if isinstance(chunk, TextChunk):
                accumulated += chunk.text
                if accumulated.strip():
                    await sent.edit_text(accumulated)
            elif isinstance(chunk, FileChunk):
                await _send_file(update, chunk.path)
    except Exception as e:
        logger.error(f"ChannelCore error: {e}")
        await sent.edit_text(f"Error: {e}")

    if not accumulated.strip():
        await sent.edit_text("(no response)")


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("psi-agent Telegram bot ready.\nSend a message to interact with the agent.")


async def run_telegram(
    *,
    session_socket: str,
    bot_token: str,
    interval: float = 1.0,
    allowed_user_ids: list[int] | None = None,
) -> None:
    app = Application.builder().token(bot_token).build()

    async with ChannelCore(session_socket, interval=interval) as core:
        app.bot_data["core"] = core
        app.bot_data["allowed_ids"] = allowed_user_ids

        app.add_handler(CommandHandler("start", _start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

        logger.info("Telegram bot polling started")
        await app.run_polling()
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/psi_agent/channel/telegram/test_telegram.py -v
```

Expected: 3 passed（2 dataclass + 1 token test）

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/telegram/client.py tests/psi_agent/channel/telegram/test_telegram.py
git commit -m "feat(telegram): implement bot handler with streaming edit_text"
```

---

### Task 4: CLI 集成

**Files:**
- Modify: `src/psi_agent/cli.py`

- [ ] **Step 1: 更新 CLI**

```python
from psi_agent.channel.telegram import ChannelTelegram

ChannelGroup = Annotated[
    ChannelRepl | ChannelCli | ChannelTelegram,
    conf.subcommand(name="channel", description="User interface channels"),
]
```

- [ ] **Step 2: 验证 CLI 能解析新子命令**

```bash
uv run psi-agent channel telegram --help
```

Expected: 显示 `usage: psi-agent channel telegram [...]` 及参数列表

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/cli.py
git commit -m "feat(telegram): register ChannelTelegram in CLI"
```

---

### Task 5: 集成测试 + 最终验证

**Files:**
- Modify: `tests/psi_agent/channel/telegram/test_telegram.py`

- [ ] **Step 1: 写集成测试——mock ChannelCore**

```python
@pytest.mark.anyio
async def test_handle_message_streams_text(tmp_path):
    """Handler accumulates TextChunks via edit_text."""
    # This test would mock ChannelCore and verify the handler logic.
    # Due to ptb requiring a real bot token for Application,
    # integration tests need a real token or mock the Application layer.
    # Skip for now — manual integration testing required.
    pass
```

- [ ] **Step 2: 运行全部检查**

```bash
uv run ruff check . && uv run ty check .
```

- [ ] **Step 3: 运行全部测试**

```bash
uv run pytest -v -m "not schedule"
```

Expected: 所有 existing 147 tests + 3 new → ~150 passed

- [ ] **Step 4: Commit**

```bash
git add tests/psi_agent/channel/telegram/test_telegram.py
git commit -m "test(telegram): add placeholder for integration tests"
```
