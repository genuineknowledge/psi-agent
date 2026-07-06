# Session 协议分离重构 实施计划

> [!NOTE]
> **Historical plan — implementation has evolved.**  The original plan
> used ``_save_history()`` / ``_load_history()`` module-level functions.
> The current ``Conversation`` class uses turn-level snapshot/rollback.
> See ``src/psi_agent/session/AGENTS.md`` for the authoritative design.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `SessionAgent.run()` 重构为纯业务逻辑，把 AI 侧和 Channel 侧的协议编解码分别抽到 `AiClient` 和 `ChannelAdapter` 中。

**Architecture:** `AiClient` ↔ `ChannelAdapter` 两侧对称协议适配器包裹纯核心 `SessionAgent.run()`。run() 消费 `AiDelta`（AI 产出）、yield `AgentChunk`（语义输出），错误通过 `AgentError` 异常传递。

**Tech Stack:** Python 3.14, anyio, aiohttp, dataclasses

---

### Task 1: 新增内部类型 `AiDelta`, `AgentChunk`, `AgentError`

**Files:**
- Modify: `src/psi_agent/session/protocol.py`
- Test: `tests/psi_agent/session/test_protocol.py`

- [ ] **Step 1: 在 protocol.py 末尾添加三个新类型**

```python
class AgentError(Exception):
    """Raised by run() when the agent encounters an unrecoverable error."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class AgentChunk:
    """Semantic output of the agent loop — content and/or reasoning."""

    content: str | None = None
    reasoning: str | None = None


@dataclass
class AiDelta:
    """Internal stream element from AiClient, consumed by run() to drive the agent loop."""

    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[dict] | None = None
    finish_reason: str | None = None
```

- [ ] **Step 2: 在 test_protocol.py 中添加单元测试**

```python
from psi_agent.session.protocol import AgentChunk, AgentError, AiDelta


def test_agent_chunk_defaults():
    c = AgentChunk()
    assert c.content is None
    assert c.reasoning is None


def test_agent_chunk_with_content():
    c = AgentChunk(content="hello")
    assert c.content == "hello"
    assert c.reasoning is None


def test_agent_chunk_with_reasoning():
    c = AgentChunk(reasoning="thinking...")
    assert c.content is None
    assert c.reasoning == "thinking..."


def test_agent_chunk_with_both():
    c = AgentChunk(content="world", reasoning="thinking...")
    assert c.content == "world"
    assert c.reasoning == "thinking..."


def test_ai_delta_defaults():
    d = AiDelta()
    assert d.content is None
    assert d.reasoning is None
    assert d.tool_calls is None
    assert d.finish_reason is None


def test_ai_delta_full():
    d = AiDelta(content="hi", reasoning="r", tool_calls=[{}], finish_reason="stop")
    assert d.content == "hi"
    assert d.reasoning == "r"
    assert d.tool_calls == [{}]
    assert d.finish_reason == "stop"


def test_agent_error_init():
    e = AgentError("something went wrong")
    assert e.message == "something went wrong"
    assert str(e) == "something went wrong"


def test_agent_error_is_exception():
    e = AgentError("test")
    assert isinstance(e, Exception)
```

- [ ] **Step 3: 运行测试确认通过**

```bash
uv run pytest tests/psi_agent/session/test_protocol.py -v -k "agent_chunk or ai_delta or agent_error"
```

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/session/protocol.py tests/psi_agent/session/test_protocol.py
git commit -m "feat(session): add AiDelta, AgentChunk, AgentError types"
```

---

### Task 2: 创建 `AiClient` 类

**Files:**
- Create: `src/psi_agent/session/ai_client.py`
- Test: `tests/psi_agent/session/test_ai_client.py`

- [ ] **Step 1: 编写 AiClient 测试**

```python
from __future__ import annotations

import asyncio
import json
import socket as _s
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.session.ai_client import AiClient
from psi_agent.session.protocol import AiDelta


@pytest.mark.anyio
async def test_ai_client_simple_content():
    """AiClient yields AiDelta with content and finish_reason from SSE."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for data in [
            json.dumps({"id": "0", "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}),
            json.dumps({"id": "1", "choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]}),
        ]:
            await resp.write(f"data: {data}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) >= 2
        contents = [d.content or "" for d in deltas]
        assert "Hello" in "".join(contents)
        assert "world" in "".join(contents)
        assert deltas[-1].finish_reason == "stop"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_tool_calls():
    """AiClient passes through partial tool_calls without accumulation."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        tc_chunk = {
            "id": "t",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":'}}
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        await resp.write(f"data: {json.dumps(tc_chunk)}\n\n".encode())
        tc_chunk2 = {
            "id": "t2",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"Beijing"}'}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        await resp.write(f"data: {json.dumps(tc_chunk2)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) >= 2
        assert deltas[-1].finish_reason == "tool_calls"
        # Verify tool_calls are passed through (partial)
        tc_list = deltas[0].tool_calls or []
        assert len(tc_list) == 1
        assert tc_list[0]["function"]["name"] == "get_weather"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_non_200():
    """Non-200 response yields AiDelta with finish_reason='error'."""

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": "bad request"}, status=400)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].finish_reason == "error"
        assert "400" in (deltas[0].content or "")
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_multi_choice_error():
    """Multiple choices (>1) yields AiDelta with finish_reason='error'."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {"id": "x", "choices": [{"delta": {}}, {"delta": {}}]}
        await resp.write(f"data: {json.dumps(data)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].finish_reason == "error"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_empty_choices_skipped():
    """0 choices → heartbeat, skipped (no AiDelta yielded)."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"id":"h","choices":[]}\n\n')
        await resp.write(
            b'data: '
            + json.dumps({"id": "r", "choices": [{"delta": {"content": "real"}, "finish_reason": "stop"}]}).encode()
            + b'\n\n'
        )
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].content == "real"
        assert deltas[0].finish_reason == "stop"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_non_data_sse_skipped():
    """SSE lines not starting with 'data: ' are skipped."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b":comment\n")
        await resp.write(b"event: ping\ndata: {}\n\n")
        await resp.write(
            b"data: "
            + json.dumps({"id": "t", "choices": [{"delta": {"content": "real"}, "finish_reason": "stop"}]}).encode()
            + b"\n\n"
        )
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].content == "real"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_null_delta_converted():
    """When delta is null (not a dict), it's treated as empty dict."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {"id": "x", "choices": [{"delta": None, "finish_reason": "stop"}]}
        await resp.write(f"data: {json.dumps(data)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].finish_reason == "stop"
        assert deltas[0].content is None
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_malformed_json_skipped():
    """Malformed JSON in SSE data line is skipped with no crash."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data: not json\n\n")
        await resp.write(
            b"data: "
            + json.dumps({"id": "g", "choices": [{"delta": {"content": "good"}, "finish_reason": "stop"}]}).encode()
            + b"\n\n"
        )
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].content == "good"
    finally:
        await runner.cleanup()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/psi_agent/session/test_ai_client.py -v
```
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: 实现 `AiClient`**

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import aiohttp
from loguru import logger

from psi_agent._socket import resolve_connector_and_endpoint
from psi_agent.session.protocol import AiDelta


class AiClient:
    """Protocol adapter for the AI backend — handles HTTP/SSE and yields AiDelta."""

    def __init__(self, ai_socket: str) -> None:
        self.ai_socket = ai_socket

    def _build_connector_and_endpoint(self) -> tuple[aiohttp.BaseConnector, str]:
        return resolve_connector_and_endpoint(self.ai_socket)

    async def stream(self, request_body: dict) -> AsyncIterator[AiDelta]:
        connector, endpoint = self._build_connector_and_endpoint()
        async with (
            aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None)) as session,
            session.post(endpoint, json=request_body) as resp,
        ):
            logger.info(f"AI response status: {resp.status}")
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI error: {error_text[:500]}")
                yield AiDelta(finish_reason="error", content=f"[AI Error: {resp.status}]")
                return

            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    continue

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse SSE data: {data_str[:100]}")
                    continue

                choices_data = data.get("choices", [])
                if len(choices_data) > 1:
                    logger.warning(f"Expected 1 choice, got {len(choices_data)}, yielding error")
                    yield AiDelta(finish_reason="error", content=f"[AI Error: expected 1 choice, got {len(choices_data)}]")
                    return
                if not choices_data:
                    continue

                c = choices_data[0]
                delta_data = c.get("delta")
                if not isinstance(delta_data, dict):
                    delta_data = {}
                yield AiDelta(
                    content=delta_data.get("content"),
                    reasoning=delta_data.get("reasoning"),
                    tool_calls=delta_data.get("tool_calls"),
                    finish_reason=c.get("finish_reason"),
                )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/psi_agent/session/test_ai_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/ai_client.py tests/psi_agent/session/test_ai_client.py
git commit -m "feat(session): add AiClient protocol adapter"
```

---

### Task 3: 创建 `ChannelAdapter`

**Files:**
- Create: `src/psi_agent/session/channel_adapter.py`
- Test: `tests/psi_agent/session/test_channel_adapter.py`

- [ ] **Step 1: 编写 ChannelAdapter 测试**

```python
from __future__ import annotations

import asyncio
import json
import socket as _s
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.protocol import AgentChunk, AgentError, ChatCompletionChunk, DeltaMessage, StreamChoice


def test_to_chat_completion_chunk_content_only():
    agent_chunk = AgentChunk(content="hello")
    cc_chunk = ChannelAdapter.to_chat_completion_chunk(agent_chunk)
    assert cc_chunk.choices[0].delta.content == "hello"
    assert cc_chunk.choices[0].delta.reasoning is None
    assert cc_chunk.choices[0].finish_reason is None


def test_to_chat_completion_chunk_reasoning_only():
    agent_chunk = AgentChunk(reasoning="thinking...")
    cc_chunk = ChannelAdapter.to_chat_completion_chunk(agent_chunk)
    assert cc_chunk.choices[0].delta.content is None
    assert cc_chunk.choices[0].delta.reasoning == "thinking..."
    assert cc_chunk.choices[0].finish_reason is None


def test_to_chat_completion_chunk_both():
    agent_chunk = AgentChunk(content="world", reasoning="thinking...")
    cc_chunk = ChannelAdapter.to_chat_completion_chunk(agent_chunk)
    assert cc_chunk.choices[0].delta.content == "world"
    assert cc_chunk.choices[0].delta.reasoning == "thinking..."


@pytest.mark.anyio
async def test_channel_adapter_integration_valid_request(tmp_path: Path):
    """Full flow: valid request → agent yields chunks → SSE response."""

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})

    async def fake_run(user_message, extra_params=None):
        yield AgentChunk(content="hello")
        yield AgentChunk(content=" world")

    agent.run = fake_run

    app = web.Application()
    lock = anyio.Lock()

    async def handler(request: web.Request) -> web.StreamResponse:
        return await ChannelAdapter.handle(request, agent, lock)

    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()
    try:
        await anyio.sleep(0.1)
        all_chunks: list[dict] = []
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post(
                "http://localhost/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            assert resp.status == 200
            async for raw in resp.content:
                line = raw.decode().strip()
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    all_chunks.append(json.loads(line[6:]))
        contents = "".join(c["choices"][0]["delta"].get("content", "") for c in all_chunks)
        assert "hello" in contents
        assert "world" in contents
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_channel_adapter_agent_error(tmp_path: Path):
    """Agent raises AgentError → ChannelAdapter writes error SSE chunk."""

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})

    async def fake_run(user_message, extra_params=None):
        raise AgentError("test error message")

    agent.run = fake_run

    app = web.Application()
    lock = anyio.Lock()

    async def handler(request: web.Request) -> web.StreamResponse:
        return await ChannelAdapter.handle(request, agent, lock)

    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()
    try:
        await anyio.sleep(0.1)
        all_chunks: list[dict] = []
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post(
                "http://localhost/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            async for raw in resp.content:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    all_chunks.append(line[6:])
        all_text = "".join(all_chunks)
        assert "test error message" in all_text
        assert "error" in all_text.lower()
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_channel_adapter_invalid_json_body(tmp_path: Path):
    """Non-JSON body → 400 response."""

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})
    lock = anyio.Lock()

    app = web.Application()

    async def handler(request: web.Request) -> web.StreamResponse:
        return await ChannelAdapter.handle(request, agent, lock)

    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()
    try:
        await anyio.sleep(0.1)
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post("http://localhost/chat/completions", data="not json") as resp,
        ):
            assert resp.status == 400
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_channel_adapter_empty_messages(tmp_path: Path):
    """Empty messages list → 400 response."""

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})
    lock = anyio.Lock()

    app = web.Application()

    async def handler(request: web.Request) -> web.StreamResponse:
        return await ChannelAdapter.handle(request, agent, lock)

    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()
    try:
        await anyio.sleep(0.1)
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post(
                "http://localhost/chat/completions",
                json={"messages": [], "stream": True},
            ) as resp,
        ):
            assert resp.status == 400
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_channel_adapter_non_agent_exception(tmp_path: Path):
    """Unexpected exception → error SSE chunk with Session Error."""

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})

    async def fake_run(user_message, extra_params=None):
        raise RuntimeError("boom")

    agent.run = fake_run

    app = web.Application()
    lock = anyio.Lock()

    async def handler(request: web.Request) -> web.StreamResponse:
        return await ChannelAdapter.handle(request, agent, lock)

    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()
    try:
        await anyio.sleep(0.1)
        all_chunks: list[str] = []
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post(
                "http://localhost/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            async for raw in resp.content:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    all_chunks.append(line[6:])
        all_text = "".join(all_chunks)
        assert "Session Error" in all_text
        assert "boom" in all_text
    finally:
        await runner.cleanup()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/psi_agent/session/test_channel_adapter.py -v
```
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: 实现 `ChannelAdapter`**

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.protocol import AgentChunk, AgentError, ChatCompletionChunk, DeltaMessage, StreamChoice


class ChannelAdapter:
    """Protocol adapter for the Channel side — parse request, convert AgentChunk → SSE."""

    @staticmethod
    async def handle(request: web.Request, agent: "SessionAgent", lock: anyio.Lock) -> web.StreamResponse:  # type: ignore[name-defined]
        try:
            user_message, extra_params = await ChannelAdapter.parse_request(request)
        except ChannelAdapter._ParseError as e:
            return web.json_response(
                {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
                status=400,
            )

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        async with lock:
            await response.prepare(request)
            logger.info("Acquired session lock, processing request")
            try:
                async for chunk in agent.run(user_message, extra_params=extra_params):
                    await response.write(ChannelAdapter.to_chat_completion_chunk(chunk).to_sse().encode())
                    logger.debug(f"Chunk sent: content={chunk.content!r}, reasoning={chunk.reasoning!r}")
                await response.write(b"data: [DONE]\n\n")
            except AgentError as e:
                err_chunk = ChatCompletionChunk(
                    id="error",
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content=f"[Session Error: {e.message}]"),
                            finish_reason="error",
                        )
                    ],
                )
                await response.write(err_chunk.to_sse().encode())
                logger.warning(f"Agent error: {e.message}")
            except Exception as e:
                err_chunk = ChatCompletionChunk(
                    id="error",
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content=f"[Session Error: {e}]"),
                            finish_reason="error",
                        )
                    ],
                )
                await response.write(err_chunk.to_sse().encode())
                logger.error(f"Unexpected error in agent run: {e}")

        logger.debug("Session request completed")
        return response

    class _ParseError(Exception):
        pass

    @staticmethod
    async def parse_request(request: web.Request) -> tuple[dict, dict]:
        try:
            body: dict = await request.json()
        except Exception as e:
            raise ChannelAdapter._ParseError(str(e))

        messages = body.pop("messages", [])
        if not messages:
            raise ChannelAdapter._ParseError("No messages in request")

        user_message = messages[-1]
        if user_message.get("role") != "user":
            user_message = {"role": "user", "content": str(user_message.get("content", ""))}

        return user_message, body

    @staticmethod
    async def write_stream(chunks: AsyncIterator[AgentChunk], response: web.StreamResponse) -> None:
        async for chunk in chunks:
            await response.write(ChannelAdapter.to_chat_completion_chunk(chunk).to_sse().encode())
        await response.write(b"data: [DONE]\n\n")

    @staticmethod
    def to_chat_completion_chunk(chunk: AgentChunk) -> ChatCompletionChunk:
        delta = DeltaMessage(content=chunk.content, reasoning=chunk.reasoning)
        return ChatCompletionChunk(choices=[StreamChoice(index=0, delta=delta)])
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/psi_agent/session/test_channel_adapter.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/channel_adapter.py tests/psi_agent/session/test_channel_adapter.py
git commit -m "feat(session): add ChannelAdapter protocol adapter"
```

---

### Task 4: 重构 `SessionAgent`

**Files:**
- Modify: `src/psi_agent/session/agent.py`

- [ ] **Step 1: 修改 `__init__` 和 `create()`**

将 `ai_socket: str` 替换为 `ai_client: AiClient`。`create()` 内部构造 `AiClient`。

```python
# agent.py — 修改 SessionAgent.__init__ 参数和 create()

from psi_agent.session.ai_client import AiClient

class SessionAgent:
    def __init__(
        self,
        *,
        ai_client: AiClient,
        tools: dict[str, ToolFunction],
        tool_funcs: dict[str, Callable[..., Any]] | None = None,
        schedules: list | None = None,
        system_prompt_builder: Callable[..., Any] | None = None,
        max_tool_rounds: int = 128,
        history: list[dict] | None = None,
        history_path: Path | None = None,
    ) -> None:
        self._ai_client = ai_client
        self.tools = tools
        self._tool_funcs = tool_funcs if tool_funcs else {}
        self.schedules = schedules if schedules is not None else []
        self._system_prompt_builder = system_prompt_builder
        self.max_tool_rounds = max_tool_rounds
        self.history = history if history is not None else []
        self._history_path = history_path
        self._pending_schedule_chunks: list[AgentChunk] = []

    @classmethod
    async def create(
        cls,
        *,
        ai_socket: str,
        workspace_path: Path,
        max_tool_rounds: int = 128,
        session_id: str | None = None,
    ) -> SessionAgent:
        tools, tool_funcs = await load_tools_from_workspace(workspace_path / "tools")
        schedules = await load_schedules_from_workspace(workspace_path / "schedules")
        history, history_path = await _init_history(workspace_path, session_id)

        return cls(
            ai_client=AiClient(ai_socket),
            tools=tools,
            tool_funcs=tool_funcs,
            schedules=schedules,
            system_prompt_builder=_load_system_prompt_builder(workspace_path),
            max_tool_rounds=max_tool_rounds,
            history=history,
            history_path=history_path,
        )
```

删除原有 `ai_socket` 赋值行和 `_build_connector_and_endpoint` 方法。

- [ ] **Step 2: 重写 `run()` 使用 `AiClient.stream()` 并 yield `AgentChunk`**

替换整个 `run()` 方法体，关键变化：
1. `self._stream_ai_request(body)` → `self._ai_client.stream(body)`，消费 `AiDelta`
2. 所有 `yield ChatCompletionChunk(...)` → `yield AgentChunk(content=..., reasoning=...)`
3. `AiDelta(finish_reason="error")` → `raise AgentError(message)`
4. 不再 import `ChatCompletionChunk`, `StreamChoice`, `DeltaMessage`

```python
async def run(self, user_message: dict, extra_params: dict | None = None) -> AsyncIterator[AgentChunk]:
    if not self.history and self._system_prompt_builder is not None:
        try:
            sp = await self._system_prompt_builder()
            self.history.append({"role": "system", "content": sp})
            logger.info(f"System prompt loaded ({len(sp) if sp else 0} chars)")
        except Exception as e:
            logger.error(f"Failed to build system prompt: {e}")

    if self._pending_schedule_chunks:
        logger.info(f"Yielding {len(self._pending_schedule_chunks)} pending schedule chunk(s)")
        for chunk in self._pending_schedule_chunks:
            yield chunk
        self._pending_schedule_chunks = []

    self.history.append(user_message)
    logger.debug(f"History now has {len(self.history)} messages")

    for _round in range(self.max_tool_rounds):
        logger.debug(f"Agent loop round {_round + 1}/{self.max_tool_rounds}")

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tools.values()
        ]

        request_body: dict = {
            "messages": self.history,
            "tools": tool_defs,
            "stream": True,
        }
        if extra_params:
            request_body |= extra_params

        logger.info(f"Sending request to AI via AiClient")
        logger.debug(f"Request messages count: {len(self.history)}, tools: {len(tool_defs)}")

        finish_reason: str | None = None
        accumulated_tool_calls: dict[int, dict] = {}
        accumulated_content: str = ""
        accumulated_reasoning: str = ""

        async for delta in self._ai_client.stream(request_body):
            if delta.content:
                yield AgentChunk(content=delta.content)
                accumulated_content += delta.content
            if delta.reasoning:
                yield AgentChunk(reasoning=delta.reasoning)
                accumulated_reasoning += delta.reasoning

            if delta.finish_reason and not finish_reason:
                finish_reason = delta.finish_reason

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    acc = accumulated_tool_calls[idx]
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"):
                        acc["function"]["name"] = func["name"]
                    if func.get("arguments"):
                        acc["function"]["arguments"] += func["arguments"]

            if finish_reason == "error":
                logger.warning("AI returned error, stopping without saving to history")
                raise AgentError(accumulated_content or accumulated_reasoning or "Unknown AI error")

            if finish_reason == "stop":
                logger.debug("AI finished with stop")
                if accumulated_content or accumulated_reasoning:
                    assistant_msg: dict = {"role": "assistant"}
                    if accumulated_content:
                        assistant_msg["content"] = accumulated_content
                    if accumulated_reasoning:
                        assistant_msg["reasoning"] = accumulated_reasoning
                    self.history.append(assistant_msg)
                    if self._history_path is not None:
                        await _save_history(self._history_path, self.history)
                return

            if finish_reason == "tool_calls":
                logger.info("AI requested tool calls, processing...")

                ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                assistant_msg: dict = {"role": "assistant", "tool_calls": ordered_calls}
                if accumulated_content:
                    assistant_msg["content"] = accumulated_content
                if accumulated_reasoning:
                    assistant_msg["reasoning"] = accumulated_reasoning
                self.history.append(assistant_msg)

                for tc in ordered_calls:
                    func_info = tc.get("function", {})
                    func_name = func_info.get("name", "")
                    func_args_str = func_info.get("arguments", "{}")

                    try:
                        args = json.loads(func_args_str)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Failed to parse tool call arguments: {func_args_str[:200]}")
                        args = {}

                    logger.info(f"Executing tool: {func_name}({args})")

                    yield AgentChunk(
                        reasoning=f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]"
                    )

                    func = self._tool_funcs.get(func_name)
                    if func is None:
                        result = f"Error: Tool '{func_name}' not found"
                        logger.error(result)
                    else:
                        try:
                            result = await func(**args)
                            logger.info(f"Tool result ({func_name}): {str(result)[:200]}")
                        except Exception as e:
                            result = f"Error executing tool '{func_name}': {e}"
                            logger.error(result)

                    yield AgentChunk(reasoning=f"[Tool Result: {str(result)[:500]}]")

                    self.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "name": func_name,
                            "content": str(result),
                        }
                    )

                break

        # SSE stream ended without a recognised finish_reason
        if finish_reason not in ("error", "stop", "tool_calls"):
            logger.warning(
                f"Unexpected finish_reason={finish_reason!r}, "
                f"saving {len(accumulated_content)} chars of content and stopping"
            )
            if accumulated_content or accumulated_reasoning:
                assistant_msg: dict = {"role": "assistant"}
                if accumulated_content:
                    assistant_msg["content"] = accumulated_content
                if accumulated_reasoning:
                    assistant_msg["reasoning"] = accumulated_reasoning
                self.history.append(assistant_msg)
            return

    else:
        logger.warning(f"Reached max tool rounds ({self.max_tool_rounds}), stopping")
        yield AgentChunk(content="[Max tool rounds reached]")
```

- [ ] **Step 3: 删除 `_stream_ai_request` 和 `_build_connector_and_endpoint` 方法**

从 agent.py 中完全删除这两个方法定义。

- [ ] **Step 4: 删除 `handle_chat_completions` 方法**

从 `SessionAgent` 类中完全删除 `handle_chat_completions` 方法定义。

- [ ] **Step 5: 修改 `set_pending_schedule_chunks` 参数类型**

```python
def set_pending_schedule_chunks(self, chunks: list[AgentChunk]) -> None:
    self._pending_schedule_chunks = chunks
```

- [ ] **Step 6: 清理 import**

```python
# agent.py — 更新 imports
from psi_agent.session.ai_client import AiClient
from psi_agent.session.protocol import AgentChunk, AgentError, ToolFunction
# 不再 import: ChatCompletionChunk, DeltaMessage, StreamChoice
# 不再需要: resolve_connector_and_endpoint (已移到 AiClient)
```

- [ ] **Step 7: 运行单元测试验证 SessionAgent 变化**

```bash
uv run pytest tests/psi_agent/session/test_session.py -v
```
这些测试测试 `_load_system_prompt_builder` 和 Session 初始化，不依赖 agent loop。

- [ ] **Step 8: Commit**

```bash
git add src/psi_agent/session/agent.py
git commit -m "refactor(session): SessionAgent uses AiClient and yields AgentChunk"
```

---

### Task 5: 适配 `__init__.py`、`server.py`、`scheduler.py`

**Files:**
- Modify: `src/psi_agent/session/__init__.py`
- Modify: `src/psi_agent/session/server.py`
- Modify: `src/psi_agent/session/scheduler.py`

- [ ] **Step 1: 修改 `__init__.py` — Session.run() 编排层**

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.scheduler import run_one_schedule
from psi_agent.session.server import serve_session


@dataclass
class Session:
    """Start a session backed by a workspace and AI."""

    channel_socket: str
    ai_socket: str
    workspace: str = ""
    max_tool_rounds: int = 128
    verbose: bool = False
    session_id: str | None = None

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = Path.cwd() if self.workspace == "" else Path(str(await anyio.Path(self.workspace).resolve()))
        logger.info(f"Loading workspace from {workspace_path}")

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

- [ ] **Step 2: 简化 `server.py` — 去掉 `app["lock"]`**

```python
from __future__ import annotations

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._socket import create_site


async def serve_session(*, channel_socket: str, handler: Handler) -> None:
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = create_site(runner, channel_socket)
    await site.start()

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        await runner.cleanup()
```

变化：去掉 `lock` 参数，去掉 `app["lock"]`

- [ ] **Step 3: 修改 `scheduler.py` — 使用 `AgentChunk`**

```python
from psi_agent.session.protocol import AgentChunk
```

在 `run_one_schedule` 中将 `pending_chunks` 类型从 `list` 改为 `list[AgentChunk]`：

```python
async def run_one_schedule(schedule: Schedule, agent: SessionAgent, lock: anyio.Lock) -> None:
    logger.info(f"Schedule runner started: {schedule.name} ({schedule.cron})")

    cron_iter = croniter(schedule.cron, time.time())

    while True:
        next_run = cron_iter.get_next()
        wait = max(0.0, next_run - time.time())
        await anyio.sleep(wait)

        try:
            logger.info(f"Schedule triggered: {schedule.name}")
            msg = {"role": "user", "content": schedule.task_content}

            async with lock:
                pending_chunks: list[AgentChunk] = []
                async for chunk in agent.run(msg):
                    pending_chunks.append(chunk)
                agent.set_pending_schedule_chunks(pending_chunks)
                logger.info(f"Schedule {schedule.name} response stored ({len(pending_chunks)} chunks)")
        except Exception as e:
            logger.error(f"Error processing schedule {schedule.name}: {e}")
```

（唯一变化是 `pending_chunks` 类型标注 `list` → `list[AgentChunk]`）

- [ ] **Step 4: 运行 lint 和 typecheck**

```bash
uv run ruff check src/psi_agent/session/
uv run ty check src/psi_agent/session/
```

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/__init__.py src/psi_agent/session/server.py src/psi_agent/session/scheduler.py
git commit -m "refactor(session): wire ChannelAdapter into Session.run, simplify server"
```

---

### Task 6: 适配 `test_agent.py`

**Files:**
- Modify: `tests/psi_agent/session/test_agent.py`

所有现有测试需要用新接口重写：
- `SessionAgent(ai_socket=...)` → `SessionAgent(ai_client=AiClient(...))` 或 `SessionAgent.create(ai_socket=...)`
- Agent yields `AgentChunk` 不再 `ChatCompletionChunk`
- 断言从 `chunk.choices[0].delta.content` → `chunk.content`
- `test_agent_ai_non_200_response` 等 error 测试需要 `pytest.raises(AgentError)`

- [ ] **Step 1: 重写 `test_agent_simple_response`**

```python
@pytest.mark.anyio
async def test_agent_simple_response(tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="Hello").encode())
        await resp.write(_sse_chunk(content=" world", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = await SessionAgent.create(ai_socket=ai_socket, workspace_path=tmp_path)
        user_msg = {"role": "user", "content": "hi"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        all_content = "".join(c.content or "" for c in chunks)
        assert "Hello world" in all_content
    finally:
        await mock_server.cleanup()
```

- [ ] **Step 2: 重写 `test_agent_with_tool_call`**

```python
@pytest.mark.anyio
async def test_agent_with_tool_call(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "get_weather.py").write_text(
        textwrap.dedent("""\
        async def get_weather(city: str) -> str:
            \"\"\"Get weather for a city.

            Args:
                city: The city name.
            \"\"\"
            return f"Weather in {city}: sunny, 22 C"
    """)
    )

    tools, _ = await load_tools_from_workspace(tools_dir)

    request_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal request_count
        await request.json()
        request_count += 1

        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)

        if request_count == 1:
            tc_chunk = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": '{"city": "Beijing"}'},
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            }
            await resp.write(f"data: {json.dumps(tc_chunk)}\n\n".encode())
            tc_chunk2 = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }
            await resp.write(f"data: {json.dumps(tc_chunk2)}\n\n".encode())
        else:
            await resp.write(_sse_chunk(content="The weather in Beijing is sunny, 22 C", finish="stop").encode())

        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), tools=tools, tool_funcs={"get_weather": _get_weather})

        user_msg = {"role": "user", "content": "What's the weather in Beijing?"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        reasoning = [c.reasoning for c in chunks if c.reasoning]
        assert len(reasoning) > 0, f"No reasoning chunks, got {len(chunks)} total"
        assert any("get_weather" in (r or "") for r in reasoning)

        content = [c.content for c in chunks if c.content]
        assert any("sunny" in (c or "") for c in content)

        assert request_count >= 2
    finally:
        await mock_server.cleanup()
```

- [ ] **Step 3: 重写 `test_agent_pending_schedule_response`**

```python
@pytest.mark.anyio
async def test_agent_pending_schedule_response(tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="Current response", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), tools={})
        agent.set_pending_schedule_chunks(
            [
                AgentChunk(reasoning="[Schedule triggered: daily report]"),
                AgentChunk(content="Schedule content here"),
            ]
        )

        user_msg = {"role": "user", "content": "hi"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        reasoning = [c.reasoning for c in chunks if c.reasoning]
        assert any("Schedule triggered" in (r or "") for r in reasoning)

        content = [c.content for c in chunks if c.content]
        assert any("Current response" in (c or "") for c in content)

        assert agent._pending_schedule_chunks == []
    finally:
        await mock_server.cleanup()
```

- [ ] **Step 4: 重写 error 路径测试（现在 raise AgentError）**

```python
@pytest.mark.anyio
async def test_agent_ai_non_200_response(tmp_path: Path) -> None:
    """AI returning non-200 should raise AgentError."""

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": "bad request"}, status=400)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tools={})
        with pytest.raises(AgentError) as exc_info:
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass
        assert "400" in exc_info.value.message
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_ai_error_not_in_history(tmp_path: Path) -> None:
    """AI error should not be appended to conversation history."""

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": "bad request"}, status=400)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tools={})
        history_len_before = len(agent.history)
        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass
        assert len(agent.history) == history_len_before + 1  # only user message added
    finally:
        await runner.cleanup()
```

- [ ] **Step 5: 重写其余 agent loop 测试 — 统一适配模式**

所有剩余测试遵循相同的机械变换模式：

| 旧写法 | 新写法 |
|--------|--------|
| `SessionAgent(ai_socket=url, ...)` | `SessionAgent(ai_client=AiClient(url), ...)` |
| `chunk.choices[0].delta.content` | `chunk.content` |
| `chunk.choices[0].delta.reasoning` | `chunk.reasoning` |
| `c.choices[0].delta.content for c in chunks` | `c.content for c in chunks` |
| `c.choices[0].delta.reasoning for c in chunks` | `c.reasoning for c in chunks` |
| `len(chunks) >= 1` (on error test) | `pytest.raises(AgentError)` (non-200/multi-choice error) |

需适配的测试：`test_agent_history_accumulation`, `test_agent_tool_not_registered`, `test_agent_tool_throws_exception_unit`, `test_agent_tool_returns_int`, `test_agent_tcp_connector`, `test_agent_non_data_sse_line`, `test_agent_empty_content_stop`。所有函数签名保持不变（`@pytest.mark.anyio`, `async def`, `tmp_path: Path`）。

- [ ] **Step 6: 重写 history persistence 测试**

`test_histories_dir_and_gitignore_created` 测试中 `SessionAgent.create(ai_socket=...)` 现在需要 workspace 中有 `tools/` 和 `schedules/` 目录存在（即使为空），以及 `systems/system.py`（如果存在）。

已有 `tools_dir.mkdir()` 和 `schedules_dir.mkdir()`，保持即可。

`test_history_saved_after_stop`, `test_history_not_saved_on_error`, `test_load_history_*`, `test_save_and_load_roundtrip` 等单独测试 `_load_history`/`_save_history`/`_init_history` 函数，不依赖 agent loop，只需确保 import 路径不变。

- [ ] **Step 7: 更新 imports**

```python
from psi_agent.session.agent import SessionAgent, _load_history, _save_history
from psi_agent.session.ai_client import AiClient
from psi_agent.session.protocol import AgentChunk, AgentError, ToolFunction
from psi_agent.session.tools import load_tools_from_workspace
```

- [ ] **Step 8: 运行 agent 测试**

```bash
uv run pytest tests/psi_agent/session/test_agent.py -v
```

- [ ] **Step 9: Commit**

```bash
git add tests/psi_agent/session/test_agent.py
git commit -m "test(session): adapt agent tests to new AiClient/AgentChunk interface"
```

---

### Task 7: 适配 `test_server.py`

**Files:**
- Modify: `tests/psi_agent/session/test_server.py`

- [ ] **Step 1: 用 `ChannelAdapter.handle` 替换 `agent.handle_chat_completions`**

所有测试中：
- `app["lock"] = lock` → 删除
- `app.router.add_post("/chat/completions", agent.handle_chat_completions)` → handler 用 ChannelAdapter

```python
# 新 handler 模式：
async def handler(request: web.Request) -> web.StreamResponse:
    return await ChannelAdapter.handle(request, agent, lock)

app.router.add_post("/chat/completions", handler)
```

- [ ] **Step 2: 重写 `_FailingSessionAgent`**

用新 `AgentChunk` + `AgentError`：

```python
class _FailingSessionAgent(SessionAgent):
    async def run(self, user_message: dict, extra_params: dict | None = None) -> AsyncIterator[AgentChunk]:
        yield AgentChunk(content="partial")
        raise RuntimeError("boom")
```

更新 import。

- [ ] **Step 3: 运行 server 测试**

```bash
uv run pytest tests/psi_agent/session/test_server.py -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/psi_agent/session/test_server.py
git commit -m "test(session): adapt server tests to ChannelAdapter"
```

---

### Task 8: 适配 `test_scheduler.py` 和集成测试

**Files:**
- Modify: `tests/psi_agent/session/test_scheduler.py`
- Modify: `tests/integration/test_session_tools.py`
- Modify: `tests/integration/test_session_concurrency.py`
- Modify: `tests/integration/test_session_workspace.py`

- [ ] **Step 1: 检查 test_scheduler.py — 无需改动**

`test_scheduler.py` 测试 `load_schedules_from_workspace` 和 `Schedule` dataclass，不依赖 agent loop，无需改动。

- [ ] **Step 2: 适配 in-process 集成测试**

`tests/integration/test_session_tools.py` 和 `tests/integration/test_session_workspace.py` 中的局部测试用 `SessionAgent(ai_socket=...)` 和 `agent.run()` + `c.choices[0].delta.content`，需适配：

| 旧写法 | 新写法 |
|--------|--------|
| `SessionAgent(ai_socket=base_url, ...)` | `SessionAgent(ai_client=AiClient(base_url), ...)` |
| `c.choices[0].delta.content` | `c.content` |
| `c.choices[0].delta.reasoning` | `c.reasoning` |

需要 import `AiClient`, `AgentChunk`。

**subprocess 测试无需改动**：`test_session_concurrency.py` 及 `test_session_workspace.py` 中的子进程测试通过 HTTP/SSE 通信，协议格式不变（仍为 `ChatCompletionChunk`）。

- [ ] **Step 3: 运行全部 session 测试**

```bash
uv run pytest tests/psi_agent/session/ -v
```

- [ ] **Step 4: 运行全部测试**

```bash
uv run pytest -v
```

- [ ] **Step 5: 运行 lint 和 typecheck**

```bash
uv run ruff check .
uv run ty check src/psi_agent/
```

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: adapt integration tests to new session interfaces"
```

---

### Task 9: 最终验证和清理

- [ ] **Step 1: 全面测试**

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run ty check src/psi_agent/
```

- [ ] **Step 2: 确认无遗留引用**

```bash
# 确认 agent.py 不再引用以下符号
uv run ruff check src/psi_agent/session/agent.py
# 确认没有裸 _stream_ai_request 或 _build_connector_and_endpoint
```

- [ ] **Step 3: Commit**

```bash
git add -u
git commit -m "chore: final cleanup after session refactor"
```
