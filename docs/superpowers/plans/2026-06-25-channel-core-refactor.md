# Channel 层公共部件提取 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提取 CLI/REPL 共享的 SSE 管道逻辑到 `ChannelCore`，引入 Chunk 类型和文件传输协议标记。

**Architecture:** `ChannelCore` 作为 async context manager 管理 aiohttp 连接，`post(list[Chunk]) -> AsyncIterator[Chunk]` 处理 Chunk→string 转换、SSE 流解析、缓冲合并、[SEND] 检测。CLI/REPL 瘦身为 thin client——只处理 Chunk 分发。

**Tech Stack:** aiohttp, anyio, re, time, dataclasses

**Design Spec:** `docs/superpowers/specs/2026-06-25-channel-core-refactor.md`

---

## File Structure

| 操作 | 文件 | 职责 |
|------|------|------|
| New | `src/psi_agent/channel/_types.py` | `FileChunk`, `TextChunk`, `Chunk` |
| New | `src/psi_agent/channel/_core.py` | `ChannelCore` — connection + SSE pipe |
| New | `tests/psi_agent/channel/__init__.py` | test package |
| New | `tests/psi_agent/channel/test__types.py` | type tests |
| New | `tests/psi_agent/channel/test__core.py` | core unit tests |
| Modify | `src/psi_agent/channel/cli/client.py` | 74→~18行 |
| Modify | `src/psi_agent/channel/repl/client.py` | 96→~41行 |
| Modify | `src/psi_agent/channel/AGENTS.md` | 反映新架构 |

---

### Task 1: Chunk 类型

**Files:**
- Create: `src/psi_agent/channel/_types.py`
- Create: `tests/psi_agent/channel/__init__.py`
- Create: `tests/psi_agent/channel/test__types.py`

- [ ] **Step 1: 创建 Chunk 类型**

```python
# src/psi_agent/channel/_types.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileChunk:
    path: str


@dataclass
class TextChunk:
    text: str


Chunk = FileChunk | TextChunk
```

- [ ] **Step 2: 创建 test 包**

```python
# tests/psi_agent/channel/__init__.py
```

- [ ] **Step 3: 写测试**

```python
# tests/psi_agent/channel/test__types.py
from __future__ import annotations

from psi_agent.channel._types import FileChunk, TextChunk


def test_file_chunk_construction():
    fc = FileChunk("/tmp/foo.txt")
    assert fc.path == "/tmp/foo.txt"


def test_text_chunk_construction():
    tc = TextChunk("hello world")
    assert tc.text == "hello world"


def test_chunk_union_isinstance():
    fc = FileChunk("/a.txt")
    tc = TextChunk("hi")

    assert isinstance(fc, FileChunk)
    assert isinstance(tc, TextChunk)
    assert not isinstance(fc, TextChunk)
    assert not isinstance(tc, FileChunk)
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/psi_agent/channel/test__types.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/channel/_types.py tests/psi_agent/channel/__init__.py tests/psi_agent/channel/test__types.py
git commit -m "feat(channel): add FileChunk, TextChunk types"
```

---

### Task 2: ChannelCore 连接管理

**Files:**
- Create: `src/psi_agent/channel/_core.py`
- Create: `tests/psi_agent/channel/test__core.py`

- [ ] **Step 1: 创建 ChannelCore 骨架（连接管理）**

```python
# src/psi_agent/channel/_core.py
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp
from aiohttp import ClientTimeout

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._types import Chunk


@dataclass
class ChannelCore:
    session_socket: str
    interval: float = 1.0

    async def __aenter__(self) -> ChannelCore:
        connector, self._endpoint = resolve_connector_and_endpoint(self.session_socket)
        self._session = aiohttp.ClientSession(
            connector=connector, timeout=ClientTimeout(total=None)
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._session.close()

    async def post(self, chunks: list[Chunk]) -> AsyncIterator[Chunk]:
        raise NotImplementedError
```

- [ ] **Step 2: 写连接管理测试**

```python
# tests/psi_agent/channel/test__core.py
from __future__ import annotations

import anyio
import pytest

from psi_agent._sockets import create_site
from psi_agent.channel._core import ChannelCore


@pytest.mark.anyio
async def test_channel_core_connect_unix(tmp_path):
    """Core can connect to a Unix socket server."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=400)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path) as core:
        assert core._session is not None
        assert core._endpoint == "http://localhost/chat/completions"

    await runner.cleanup()


@pytest.mark.anyio
async def test_channel_core_raises_on_http_error(tmp_path):
    """Core raises on non-200 response."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": {"message": "server error"}}, status=500)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import TextChunk

    async with ChannelCore(sock_path) as core:
        with pytest.raises(Exception, match="server error"):
            async for _ in core.post([TextChunk("hi")]):
                pass

    await runner.cleanup()
```

- [ ] **Step 3: 运行测试验证连接 + 错误处理**

```bash
uv run pytest tests/psi_agent/channel/test__core.py -v
```

Expected: 2 passed

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/channel/_core.py tests/psi_agent/channel/test__core.py
git commit -m "feat(channel): add ChannelCore connection management and error handling"
```

---

### Task 3: ChannelCore SSE 流处理 + 缓冲

**Files:**
- Modify: `src/psi_agent/channel/_core.py` — 实现 `post()` SSE 逻辑

- [ ] **Step 1: 实现 post() SSE 解析 + [RECV] 拼接 + [SEND] 检测 + 缓冲**

替换 `post()` 的 `raise NotImplementedError` 为：

```python
import json
import re
import time

from psi_agent.channel._types import Chunk, FileChunk, TextChunk
```

```python
async def post(self, chunks: list[Chunk]) -> AsyncIterator[Chunk]:
    full_buf = ""
    chunk_buf = ""
    scan_ptr = 0
    emitted: set[str] = set()

    parts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, FileChunk):
            parts.append(f"[RECV:{chunk.path}]")
        elif isinstance(chunk, TextChunk):
            parts.append(chunk.text)
    content = "\n".join(parts)

    body = {"messages": [{"role": "user", "content": content}], "stream": True}

    timer_target: float | None = None

    async with self._session.post(self._endpoint, json=body) as resp:
        if resp.status != 200:
            msg = await resp.text()
            try:
                error = json.loads(msg)
                msg = error.get("error", {}).get("message", msg)
            except Exception:
                pass
            raise Exception(msg)

        async for raw_line in resp.content:
            line = raw_line.decode().strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            for choice in data.get("choices", []):
                if choice.get("finish_reason") == "error":
                    delta = choice.get("delta", {})
                    raise Exception(delta.get("content", "Session error"))

                delta = choice.get("delta", {})
                text = delta.get("content") or ""
                if not text:
                    continue

                orig_len = len(full_buf)
                full_buf += text
                chunk_buf += text

                new = full_buf[scan_ptr:]
                for match in re.finditer(r"\[SEND:(.+?)\]", new):
                    path = match.group(1)
                    if path not in emitted:
                        yield FileChunk(path)
                        emitted.add(path)
                    scan_ptr = orig_len + match.end()

                if timer_target is None:
                    timer_target = time.monotonic() + self.interval

                if time.monotonic() >= timer_target:
                    yield TextChunk(chunk_buf)
                    chunk_buf = ""
                    timer_target = None

    if chunk_buf:
        yield TextChunk(chunk_buf)
```

- [ ] **Step 2: 写 SSE 处理测试**

追加到 `tests/psi_agent/channel/test__core.py`：

```python
@pytest.mark.anyio
async def test_post_converts_file_chunk_to_recv_marker(tmp_path):
    """FileChunk becomes [RECV:path] in the POST body."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")
    received_body = {}

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal received_body
        received_body = await request.json()
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"ok},"finish_reason":"stop"}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import FileChunk, TextChunk

    async with ChannelCore(sock_path) as core:
        chunks = []
        async for chunk in core.post([FileChunk("/home/user/file.txt"), TextChunk("hello")]):
            chunks.append(chunk)

    expected_content = "[RECV:/home/user/file.txt]\nhello"
    assert received_body["messages"][0]["content"] == expected_content
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "ok"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_sse_buffering_merges_within_interval(tmp_path):
    """SSE chunks within interval are merged into one TextChunk."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"hello "}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"world"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import TextChunk

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "hello world"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_sse_interval_split(tmp_path):
    """SSE chunks arriving after interval expiry yield separate TextChunks."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"first"}}]}\n\n')
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import TextChunk

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "first"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_detects_send_marker(tmp_path):
    """[SEND:/path] in SSE content yields FileChunk."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"Here is [SEND:/tmp/output.py] the file. more text"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import TextChunk, FileChunk

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 2
    assert isinstance(chunks[0], FileChunk)
    assert chunks[0].path == "/tmp/output.py"
    assert isinstance(chunks[1], TextChunk)
    assert "Here is [SEND:/tmp/output.py] the file. more text" in chunks[1].text

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_send_dedup(tmp_path):
    """Same [SEND] path only yields FileChunk once."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"[SEND:/a.py] chunk1 [SEND:/a.py] chunk2"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import FileChunk

    async with ChannelCore(sock_path, interval=0.0) as core:
        file_chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            if isinstance(chunk, FileChunk):
                file_chunks.append(chunk)

    assert len(file_chunks) == 1
    assert file_chunks[0].path == "/a.py"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_handles_error_chunk(tmp_path):
    """SSE chunk with finish_reason='error' raises."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"id":"error","choices":[{"index":0,"delta":{"content":"[Upstream Error 401]: bad key"},"finish_reason":"error"}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import TextChunk

    async with ChannelCore(sock_path) as core:
        with pytest.raises(Exception, match="Upstream Error 401"):
            async for _ in core.post([TextChunk("hi")]):
                pass

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_flush_on_stream_end(tmp_path):
    """Residual chunk_buf is flushed when stream ends."""
    from aiohttp import web

    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"leftover"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    from psi_agent.channel._types import TextChunk

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].text == "leftover"

    await runner.cleanup()
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/psi_agent/channel/test__core.py -v
```

Expected: 9 passed

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/channel/_core.py tests/psi_agent/channel/test__core.py
git commit -m "feat(channel): implement ChannelCore SSE streaming, buffering, SEND/RECV protocol"
```

---

### Task 4: CLI 瘦身

**Files:**
- Modify: `src/psi_agent/channel/cli/client.py`

- [ ] **Step 1: 重写 CLI client**

```python
# src/psi_agent/channel/cli/client.py
from __future__ import annotations

import sys
from contextlib import aclosing

from loguru import logger
from rich.console import Console

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk


async def run_cli(*, session_socket: str, message: str) -> None:
    if message == "-":
        message = await anyio.to_thread.run_sync(sys.stdin.read, abandon_on_cancel=True)
    console = Console(highlight=False)
    logger.info(f"Connecting to session at {session_socket}")

    try:
        async with (
            ChannelCore(session_socket, interval=0.0) as core,
            aclosing(core.post([TextChunk(message)])) as stream,
        ):
            async for chunk in stream:
                if isinstance(chunk, ReasoningChunk):
                    console.print(chunk.text, end="", style="dim")
                elif isinstance(chunk, TextChunk):
                    console.print(chunk.text, end="")
    except Exception as e:
        logger.error(f"CLI error: {e!r}")
        console.print(f"[red]Error: {e}[/red]")
        raise

    console.print()
```

- [ ] **Step 2: 清理不再需要的 import**

CLI client 不再需要 `aiohttp`、`json`、`psi_agent._sockets`。验证：

```bash
uv run ruff check src/psi_agent/channel/cli/client.py
```

- [ ] **Step 3: 运行现有集成测试**

```bash
uv run pytest tests/integration/test_channel_repl_cli.py tests/integration/test_channel_error.py -v -m "not schedule"
```

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/channel/cli/client.py
git commit -m "refactor(channel): slim CLI client to use ChannelCore"
```

---

### Task 5: REPL 瘦身

**Files:**
- Modify: `src/psi_agent/channel/repl/client.py`

- [ ] **Step 1: 重写 REPL client**

```python
# src/psi_agent/channel/repl/client.py
from __future__ import annotations

from aiohttp import ClientConnectorError
from loguru import logger
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.panel import Panel

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import TextChunk

console = Console(highlight=False)


async def run_repl(session_socket: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    prompt_session = PromptSession(multiline=True)

    try:
        async with ChannelCore(session_socket, interval=0.0) as core:
            logger.info("Connected to session. Enter for newline, Alt+Enter to send (Ctrl+D to exit).")
            console.print(Panel.fit("psi-agent REPL — Enter newline, Alt+Enter send"))
            console.print("[dim]Ctrl+D to exit[/dim]\n")

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ", prompt_continuation=". ")
                except (EOFError, KeyboardInterrupt):
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                console.print()
                try:
                    async for chunk in core.post([TextChunk(user_input)]):
                        if isinstance(chunk, TextChunk):
                            console.print(chunk.text, end="")
                except Exception as e:
                    logger.error(f"REPL error: {e}")
                    console.print(f"\n[red]Error: {e}[/red]")
                console.print("\n")

    except ClientConnectorError as e:
        console.print(f"[red]Connection error: {e}[/red]")
        raise
    except Exception as e:
        logger.exception("Unexpected REPL error")
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise
```

- [ ] **Step 2: 清理不再需要的 import**

```bash
uv run ruff check src/psi_agent/channel/repl/client.py
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/integration/test_channel_repl_cli.py tests/integration/test_channel_error.py -v -m "not schedule"
```

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/channel/repl/client.py
git commit -m "refactor(channel): slim REPL client to use ChannelCore"
```

---

### Task 6: 更新文档 + 最终验证

**Files:**
- Modify: `src/psi_agent/channel/AGENTS.md`

- [ ] **Step 1: 更新 AGENTS.md**

在 AGENTS.md 开头增加 ChannelCore 描述：

```markdown
## Channel 层架构

```
channel/
├── _types.py          # FileChunk, TextChunk, Chunk
├── _core.py           # ChannelCore — 连接管理 + SSE 管道
├── cli/
│   ├── __init__.py     # ChannelCli dataclass
│   └── client.py       # 单次消息 thin client (~18行)
└── repl/
    ├── __init__.py     # ChannelRepl dataclass
    └── client.py       # 交互式 thin client (~41行)
```

### ChannelCore

`ChannelCore` 是 CLI 和 REPL 共享的公共部件：

- async context manager，管理 aiohttp ClientSession
- `post(list[Chunk]) -> AsyncIterator[Chunk]`：Chunk → 字符串 → POST → SSE → Chunk
- 将输入中的 `FileChunk` 转换为 `[RECV:/path]` 标记（session 端负责读文件）
- 检测输出中的 `[SEND:/path]` 标记并产生 `FileChunk`
- SSE 内容在 interval 窗口内缓冲合并为单个 `TextChunk`（默认 1s，可配置）

Channel 客户端（CLI/REPL）不再直接处理 HTTP、SSE 解析或错误格式。
```

当前 AGENTS.md（32行）替换为更新后的完整内容：
- 保留 "终端输出约定"、"REPL 约定"、"CLI 约定" 三节原有内容

- [ ] **Step 2: 运行全部检查**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check .
```

- [ ] **Step 3: 运行全部测试**

```bash
uv run pytest -v -m "not schedule"
```

Expected: ~140+ tests 全绿（新增 ~11 个 channel 单元测试 + 原有 133）

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/channel/AGENTS.md
git commit -m "docs(channel): update AGENTS.md for ChannelCore architecture"
```
