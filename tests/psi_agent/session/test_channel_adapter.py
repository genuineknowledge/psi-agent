from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.protocol import AgentChunk


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
    """Full flow: valid request -> agent yields chunks -> SSE response."""

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
    """Agent raises AgentError -> ChannelAdapter writes error SSE chunk."""

    from psi_agent.session.protocol import AgentError

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})

    async def fake_run(user_message, extra_params=None):
        if False:
            yield
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
async def test_channel_adapter_invalid_json_body(tmp_path: Path):
    """Non-JSON body -> 400 response."""

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
    """Empty messages list -> 400 response."""

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
    """Unexpected exception -> error SSE chunk with Session Error."""

    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={})

    async def fake_run(user_message, extra_params=None):
        if False:
            yield
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
