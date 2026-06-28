from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from psi_agent.session._tool_registry import ToolRegistry
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.protocol import AgentChunk, AgentError


class _MockResponse:
    """Captures bytes written via ``write()``."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.written.append(data)


@pytest.mark.anyio
async def test_write_streams_agent_chunks():
    """write() consumes AgentChunk iterator and produces SSE."""

    async def chunks() -> AsyncIterator[AgentChunk]:
        yield AgentChunk(content="hello")
        yield AgentChunk(reasoning="think")
        yield AgentChunk(content="world")

    response = _MockResponse()
    await ChannelAdapter.write(response, chunks())

    all_bytes = b"".join(response.written)
    text = all_bytes.decode()
    assert "hello" in text
    assert "think" in text
    assert "world" in text
    assert text.endswith("data: [DONE]\n\n")


@pytest.mark.anyio
async def test_write_catches_agent_error():
    """write() catches AgentError and writes error chunk."""

    async def chunks() -> AsyncIterator[AgentChunk]:
        yield AgentChunk(content="partial")
        raise AgentError("something failed")

    response = _MockResponse()
    await ChannelAdapter.write(response, chunks())

    all_bytes = b"".join(response.written)
    text = all_bytes.decode()
    assert "partial" in text
    assert '"finish_reason": "error"' in text
    assert "something failed" in text


@pytest.mark.anyio
async def test_write_catches_generic_exception():
    """write() catches unexpected exceptions and writes error chunk."""

    async def chunks() -> AsyncIterator[AgentChunk]:
        yield AgentChunk(content="partial")
        raise RuntimeError("boom")

    response = _MockResponse()
    await ChannelAdapter.write(response, chunks())

    all_bytes = b"".join(response.written)
    text = all_bytes.decode()
    assert "partial" in text
    assert '"finish_reason": "error"' in text
    assert "boom" in text


@pytest.mark.anyio
async def test_handle_request_integration_valid(tmp_path: Path):
    agent = SessionAgent(ai_client=AiClient("http://nonexistent/v1"), tool_registry=ToolRegistry(tools={}))

    async def fake_run(user_message, extra_params=None):
        yield AgentChunk(content="hello")
        yield AgentChunk(content=" world")

    agent.run = fake_run

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)
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
async def test_handle_request_integration_agent_error(tmp_path: Path):
    agent = SessionAgent(ai_client=AiClient("http://nonexistent/v1"), tool_registry=ToolRegistry(tools={}))

    async def fake_run(user_message, extra_params=None):
        if False:
            yield
        raise AgentError("test error message")

    agent.run = fake_run

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)
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
        assert "test error message" in all_text
        assert "error" in all_text.lower()
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_handle_request_integration_invalid_json(tmp_path: Path):
    agent = SessionAgent(ai_client=AiClient("http://nonexistent/v1"), tool_registry=ToolRegistry(tools={}))

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)
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
async def test_handle_request_integration_non_dict_body(tmp_path: Path):
    """JSON array as body (not an object) -> 400 response."""

    agent = SessionAgent(ai_client=AiClient("http://nonexistent/v1"), tool_registry=ToolRegistry(tools={}))

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)
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
                json=[1, 2, 3],
            ) as resp,
        ):
            assert resp.status == 400
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_handle_request_integration_empty_messages(tmp_path: Path):
    agent = SessionAgent(ai_client=AiClient("http://nonexistent/v1"), tool_registry=ToolRegistry(tools={}))

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)
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
async def test_handle_request_integration_generic_exception(tmp_path: Path):
    agent = SessionAgent(ai_client=AiClient("http://nonexistent/v1"), tool_registry=ToolRegistry(tools={}))

    async def fake_run(user_message, extra_params=None):
        if False:
            yield
        raise RuntimeError("boom")

    agent.run = fake_run

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)
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
