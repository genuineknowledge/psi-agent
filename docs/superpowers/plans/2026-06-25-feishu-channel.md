# Feishu Channel 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 Feishu 通道，通过 lark-channel-sdk 的卡片流式渲染实现飞书机器人交互

**Architecture:** ChannelFeishu dataclass → `run()` 创建 FeishuChannel → `channel.on("message", handler)` → handler 调用 `core.post()` → `channel.stream()` + `ctrl.update()` 卡片流式

**Tech Stack:** lark-channel-sdk, aiohttp, ChannelCore

**Design Spec:** `docs/superpowers/specs/2026-06-25-feishu-channel.md`

---

## File Structure

| 操作 | 文件 | 职责 |
|------|------|------|
| Create | `src/psi_agent/channel/feishu/__init__.py` | ChannelFeishu dataclass + `run()` |
| Create | `src/psi_agent/channel/feishu/client.py` | handler、文件下载、主循环 |
| Modify | `src/psi_agent/cli.py` | 加入 ChannelFeishu 到 ChannelGroup |
| Modify | `src/psi_agent/_run.py` | 加入 feishu 到 YAML 分发 |
| Modify | `pyproject.toml` | 新增 lark-channel-sdk 依赖 |
| Create | `tests/psi_agent/channel/feishu/__init__.py` | test package |
| Create | `tests/psi_agent/channel/feishu/test_feishu.py` | dataclass + token validation tests |

---

### Task 1: pyproject.toml 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 lark-channel-sdk**

在 `[project].dependencies` 中添加：

```toml
    "lark-channel-sdk>=1.0",
```

- [ ] **Step 2: 安装依赖**

```bash
uv sync
```

Expected: 无错误

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add lark-channel-sdk dependency"
```

---

### Task 2: ChannelFeishu dataclass

**Files:**
- Create: `src/psi_agent/channel/feishu/__init__.py`
- Create: `tests/psi_agent/channel/feishu/__init__.py`
- Create: `tests/psi_agent/channel/feishu/test_feishu.py`

- [ ] **Step 1: 写测试**

```python
# tests/psi_agent/channel/feishu/__init__.py
```

```python
# tests/psi_agent/channel/feishu/test_feishu.py
from __future__ import annotations

import pytest

from psi_agent.channel.feishu import ChannelFeishu


def test_channel_feishu_defaults():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock")
    assert cf.session_socket == "/tmp/feishu.sock"
    assert cf.app_id == ""
    assert cf.app_secret == ""
    assert cf.interval == 1.0
    assert cf.allowed_user_ids is None
    assert cf.verbose is False


def test_channel_feishu_with_whitelist():
    cf = ChannelFeishu(
        session_socket="/tmp/feishu.sock",
        app_id="cli_abc",
        app_secret="secret123",
        interval=0.5,
        allowed_user_ids=["ou_123", "ou_456"],
        verbose=True,
    )
    assert cf.app_id == "cli_abc"
    assert cf.app_secret == "secret123"
    assert cf.interval == 0.5
    assert cf.allowed_user_ids == ["ou_123", "ou_456"]
    assert cf.verbose is True


@pytest.mark.anyio
async def test_run_raises_on_missing_app_id():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock", app_secret="secret")
    with pytest.raises(ValueError, match="app_id"):
        await cf.run()


@pytest.mark.anyio
async def test_run_raises_on_missing_app_secret():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock", app_id="cli_abc")
    with pytest.raises(ValueError, match="app_secret"):
        await cf.run()
```

- [ ] **Step 2: 验证测试 fail**

```bash
uv run pytest tests/psi_agent/channel/feishu/test_feishu.py -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: 创建目录 + 实现 ChannelFeishu**

```bash
mkdir -p src/psi_agent/channel/feishu
```

```python
# src/psi_agent/channel/feishu/__init__.py
"""Feishu bot channel."""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from psi_agent._logging import setup_logging

from .client import run_feishu


@dataclass
class ChannelFeishu:
    """Feishu bot channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe)."""

    app_id: str = ""
    """Feishu app ID (CLI arg > PSI_FEISHU_APP_ID env)."""

    app_secret: str = ""
    """Feishu app secret (CLI arg > PSI_FEISHU_APP_SECRET env)."""

    interval: float = 1.0
    """SSE buffer merge window."""

    allowed_user_ids: list[str] | None = None
    """Whitelist of open_id/user_id. None = allow all."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        app_id = self.app_id or os.environ.get("PSI_FEISHU_APP_ID", "")
        app_secret = self.app_secret or os.environ.get("PSI_FEISHU_APP_SECRET", "")
        if not app_id:
            raise ValueError("No Feishu app_id. Set --app-id or PSI_FEISHU_APP_ID.")
        if not app_secret:
            raise ValueError("No Feishu app_secret. Set --app-secret or PSI_FEISHU_APP_SECRET.")

        logger.info(f"Starting Feishu bot, connecting to {self.session_socket}")
        await run_feishu(
            session_socket=self.session_socket,
            app_id=app_id,
            app_secret=app_secret,
            interval=self.interval,
            allowed_user_ids=self.allowed_user_ids,
        )
```

```python
# src/psi_agent/channel/feishu/client.py (stub)
"""Feishu bot client."""

from __future__ import annotations


async def run_feishu(
    *,
    session_socket: str,
    app_id: str,
    app_secret: str,
    interval: float = 1.0,
    allowed_user_ids: list[str] | None = None,
) -> None:
    raise NotImplementedError
```

- [ ] **Step 4: 运行测试确认 pass**

```bash
uv run pytest tests/psi_agent/channel/feishu/test_feishu.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/feishu/ tests/psi_agent/channel/feishu/
git commit -m "feat(feishu): add ChannelFeishu dataclass and stub client"
```

---

### Task 3: Feishu client 核心逻辑

**Files:**
- Modify: `src/psi_agent/channel/feishu/client.py`

- [ ] **Step 1: 实现完整 client**

```python
# src/psi_agent/channel/feishu/client.py
"""Feishu bot client — handler, file download, streaming, main loop."""

from __future__ import annotations

from datetime import date

import anyio
import platformdirs
from loguru import logger
from lark_channel import FeishuChannel

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import FileChunk, TextChunk


def _allowed(sender_id: str, allowed_ids: list[str] | None) -> bool:
    if allowed_ids is None:
        return True
    return sender_id in allowed_ids


async def _download_file(ctx: object, file_obj: object, downloads: str) -> str | None:
    logger.debug(f"_download_file: file_name={file_obj.file_name} size={file_obj.file_size}")
    try:
        path = f"{downloads}/{file_obj.file_unique_id}"
        await ctx.download_file(file_obj, path)
        logger.debug(f"_download_file: downloaded to {path}")
        return path
    except Exception as e:
        logger.error(f"_download_file: failed — {e}")
        return None


async def _build_chunks(ctx: object, downloads: str) -> list[Chunk]:
    from psi_agent.channel._types import Chunk
    chunks: list[Chunk] = []

    if ctx.is_text or ctx.is_post:
        logger.debug(f"_build_chunks: text/post ({len(ctx.content_text)} chars)")
        chunks.append(TextChunk(ctx.content_text))

    for f in ctx.files:
        path = await _download_file(ctx, f, downloads)
        if path:
            chunks.append(FileChunk(path))

    logger.debug(f"_build_chunks: total {len(chunks)} chunk(s)")
    return chunks


async def _handle_message(ctx, core: ChannelCore, allowed_ids: list[str] | None) -> None:
    if not _allowed(ctx.sender_id, allowed_ids):
        logger.debug(f"_handle_message: sender {ctx.sender_id} blocked")
        return

    logger.debug(f"_handle_message: sender={ctx.sender_id} chat={ctx.chat_id}")

    downloads = f"{platformdirs.user_downloads_dir()}/.psi/{date.today()}"
    await anyio.Path(downloads).mkdir(parents=True, exist_ok=True)

    chunks = await _build_chunks(ctx, downloads)
    if not chunks:
        logger.debug("_handle_message: no chunks")
        return

    logger.debug(f"_handle_message: posting {len(chunks)} chunk(s)")
    try:
        async with ctx.channel.stream(ctx.chat_id, reply_to=ctx.message_id) as ctrl:
            async for chunk in core.post(chunks):
                if isinstance(chunk, TextChunk):
                    ctrl.update(chunk.text)
                    logger.debug(f"_handle_message: ctrl.update ({len(chunk.text)} chars)")
                elif isinstance(chunk, FileChunk):
                    await ctx.channel.send(ctx.chat_id, {"file": chunk.path})
                    ctrl.update(f"[SENT: {chunk.path}]")
                    logger.debug(f"_handle_message: file sent ({chunk.path})")
    except Exception as e:
        logger.error(f"_handle_message: error — {e}")


async def run_feishu(
    *,
    session_socket: str,
    app_id: str,
    app_secret: str,
    interval: float = 1.0,
    allowed_user_ids: list[str] | None = None,
) -> None:
    channel = FeishuChannel(app_id=app_id, app_secret=app_secret)
    logger.debug(f"run_feishu: FeishuChannel created (app_id={app_id})")

    async with ChannelCore(session_socket, interval=interval) as core:
        logger.debug("run_feishu: handler registered (message)")

        async def on_message(ctx):
            await _handle_message(ctx, core, allowed_user_ids)

        channel.on("message", on_message)
        logger.info(f"Feishu bot connecting (session={session_socket})")
        await channel.connect()
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/psi_agent/channel/feishu/test_feishu.py -v --no-cov
```

Expected: 4 passed

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/channel/feishu/client.py
git commit -m "feat(feishu): implement bot handler with card streaming"
```

---

### Task 4: CLI 集成

**Files:**
- Modify: `src/psi_agent/cli.py`
- Modify: `src/psi_agent/_run.py`

- [ ] **Step 1: 更新 CLI**

```python
# cli.py — add import
from psi_agent.channel.feishu import ChannelFeishu

# cli.py — update ChannelGroup
ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")]
    | Annotated[ChannelCli, conf.subcommand(name="cli")]
    | Annotated[ChannelTelegram, conf.subcommand(name="telegram")]
    | Annotated[ChannelFeishu, conf.subcommand(name="feishu")],
    conf.subcommand(name="channel", description="User interface channels"),
]
```

```python
# _run.py — add import
from psi_agent.channel.feishu import ChannelFeishu

# _run.py — add case
case "feishu":
    components.append(ChannelFeishu(**item))
```

- [ ] **Step 2: 验证 CLI**

```bash
uv run psi-agent channel feishu --help
```

Expected: 显示 `usage: psi-agent channel feishu [...]` 及参数列表

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/cli.py src/psi_agent/_run.py
git commit -m "feat(feishu): register ChannelFeishu in CLI and YAML run"
```

---

### Task 5: 最终验证

- [ ] **Step 1: 运行全部检查**

```bash
uv run ruff check . && uv run ty check .
```

- [ ] **Step 2: 运行全部测试**

```bash
uv run pytest -q -m "not schedule"
```

Expected: ~154 tests 全绿

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "test(feishu): add dataclass and token validation tests"
```

---

## 后续增强：处理状态表情回复（2026-06-28，已完成）

参考 Hermes：收到白名单消息后立即在该消息上加 `Typing` 表情，回复完成后移除；处理失败则替换为 `CrossMark`。设计详见 spec 第 11 节。

**Files:**
- Modify: `src/psi_agent/channel/feishu/client.py`
- Modify: `tests/psi_agent/channel/feishu/test_feishu.py`
- Modify: `src/psi_agent/channel/AGENTS.md`

- [x] 调研 Hermes 实际使用的 `emoji_type`（`Typing` 处理中 / `CrossMark` 失败，均为飞书官方有效）
- [x] 加常量 `_EMOJI_PROCESSING = "Typing"`、`_EMOJI_FAILED = "CrossMark"`
- [x] 实现 `_add_reaction(channel, message_id, emoji_type)` / `_remove_reaction(channel, message_id, reaction_id)`（失败安全，try/except + log，永不抛）
- [x] `_handle_and_stream` 重构：白名单后立即 `_add_reaction(Typing)`；`try` 包裹 build + stream（异常置 `failed=True`）；`finally` 中移除 `Typing`，`failed` 时加 `CrossMark`
- [x] 测试：`_add_reaction` 成功/异常、`_remove_reaction` 调用/吞异常、生命周期成功（加 Typing → 移除，不加 CrossMark）、生命周期失败（加 Typing → 移除 → 加 CrossMark）
- [x] 质量门：`uv run ruff check .` / `ruff format --check .` / `ty check` / `pytest -m "not schedule"`（168 passed）
- [x] Commit `feat(feishu): add processing-status reaction (Typing while working, CrossMark on failure)`（`36e9031`）

---

## 后续增强：群聊 @ 触发与准入策略（2026-07-20，已完成）

**目标**：飞书机器人在群里被 @ 时才用 Haitun Agent 读取消息并回复；单聊照常回复。策略可配置。设计详见 spec 第 12 节。

**根因（"群里 @ 了也不回复"）**：`lark_channel` 的 `PolicyGate` 默认 `require_mention=True`，群聊只有 @机器人才通过。@机器人判定依赖机器人 `open_id`——`FeishuChannel` 启动时调 `/bot/v3/info` 自动拉取；拉取失败（网络抖动 / 飞书后台未开启机器人能力）时 `open_id` 恒为 None，群里所有消息被判"未 @"而拒绝。psi-agent 侧此前无兜底、无日志，且从未传 `policy`。

**Files:**
- Modify: `src/psi_agent/channel/feishu/__init__.py`
- Modify: `src/psi_agent/channel/feishu/client.py`
- Modify: `tests/psi_agent/channel/feishu/test_feishu.py`
- Modify: `src/psi_agent/channel/AGENTS.md`
- Modify: `docs/superpowers/specs/2026-06-25-feishu-channel.md`（第 12 节）

- [x] `ChannelFeishu` 加 `require_mention`（默认 True）/ `respond_to_mention_all`（默认 False）字段，`run()` 透传给 `run_feishu`
- [x] `run_feishu` 构造 `lark_channel` 的 `PolicyConfig` 并经 `FeishuChannel(policy=...)` 传入（此前完全未传，只吃库默认值）
- [x] `_ensure_bot_identity(channel)`：`start_background()` 后 `bot_identity` 为 None 时 `resolve_bot_identity()` 兜底重试一次；成功记 INFO，失败记 WARNING（明确提示群 @ 检测不可用、去后台开机器人能力），自身异常不冒泡
- [x] `_log_reject(event)`：注册 `channel.on("reject")`，被策略拒的消息按原因（`policy_no_mention` 等）记 DEBUG，失败安全
- [x] 测试：新字段默认值 / 透传、`PolicyConfig` 正确构造并传入、bot 身份兜底解析 / 解析失败告警 / 异常吞掉、`reject` 回调注册
- [x] 质量门：`ruff check` / `ruff format --check` / `ty check` / `pytest tests/psi_agent/channel/feishu/`
- [x] Commit `feat(haitun/feishu): 群聊仅在 @机器人时回复 + bot 身份兜底`（`5ed14634`）；ty 修复 `69470b5b`

---

## 后续增强：群聊上下文与文档读取（2026-07-20，已完成）

**目标**：机器人被 @ 时能读群聊历史上下文及其中的文档 / 文件。设计详见 spec 第 13 节。

**缺口**：`_build_chunks` 只把消息正文发给 agent，agent 不知道自己在哪个群（`chat_id`），即便 workspace 装了 `feishu_message_list` 也无从调用。

**Files:**
- Modify: `src/psi_agent/channel/feishu/client.py`
- Modify: `tests/psi_agent/channel/feishu/test_feishu.py`
- Modify: `examples/haitun-workspace/TOOLS.md`
- Modify: `src/psi_agent/channel/AGENTS.md`
- Modify: `docs/superpowers/specs/2026-06-25-feishu-channel.md`（第 13 节）

- [x] `_context_header(ctx)`：在发给 agent 的文本最前注入 `<feishu_context>` 元数据块（chat_id / chat_type / message_id / sender_open_id，可选 sender_name / thread_id）
- [x] **刻意为之**：header 只含客观协议事实、不含具体 workspace 工具名（channel 与 workspace 工具解耦，遵守微内核理念）。引导 agent 用 chat_id 拉上下文 / 读文档的说明放 workspace 的 `TOOLS.md`
- [x] header 仅在有真实内容（文本 / 音频 / 资源）时随内容注入；无内容时丢弃 header 返回 `[]`，保持 "unsupported message type" 语义
- [x] 读取按需：是否调 `feishu_message_list` / `feishu_doc_read` / `feishu_file_download` 由 agent 决策，channel 不预拉（省 token、避免无关内容）
- [x] 测试：元数据头注入 / 群聊 chat_id 携带（且不含工具名）/ 空消息丢弃 header
- [x] 质量门：`ruff check` / `ruff format --check` / `ty check` / `pytest tests/psi_agent/channel/feishu/`（24 passed）
- [x] Commit `feat(haitun/feishu): 向 agent 注入群聊上下文元数据`（`9b1cc9bd`）；解耦 + 文档同步 `740d66b5`
