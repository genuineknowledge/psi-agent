from __future__ import annotations

import asyncio
import json
import socket as _s
import textwrap
from pathlib import Path
from typing import Any, ClassVar

import anyio
import pytest
from aiohttp import web
from anyio.lowlevel import checkpoint

from psi_agent.session import Session
from psi_agent.session.agent import (
    SessionAgent,
    _build_memory_client_config,
    _FusionMemoryClient,
    _load_history,
    _MemoryClientConfig,
    _save_history,
)
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction
from psi_agent.session.tools import load_tools_from_workspace


async def _get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Weather in {city}: sunny, 22 C"


def _sse_chunk(content: str = "", reasoning: str = "", finish: str | None = None) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning"] = reasoning
    chunk = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


class MockAIServer:
    """Helper to create and cleanup a mock AI Unix socket server."""

    def __init__(self, tmp_path: Path) -> None:
        self.socket_path = tmp_path / "ai.sock"
        self._runner: web.AppRunner | None = None
        self._app: web.Application | None = None

    async def start(self, handler) -> str:
        self._app = web.Application()
        self._app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.UnixSite(self._runner, str(self.socket_path))
        await site.start()
        return str(self.socket_path)

    async def cleanup(self) -> None:
        if self._runner:
            await self._runner.cleanup()


def test_build_memory_client_config_reads_expected_env_keys() -> None:
    config = _build_memory_client_config(
        {
            "PSI_MEMORY_BASE_URL": "http://127.0.0.1:8700",
            "PSI_MEMORY_TIMEOUT_SECONDS": "3.5",
            "PSI_MEMORY_WORKSPACE_ID": "ws",
            "PSI_MEMORY_USER_ID": "u",
            "PSI_MEMORY_AGENT_ID": "agent",
            "PSI_MEMORY_SESSION_ID": "session-1",
        }
    )

    assert config.base_url == "http://127.0.0.1:8700"
    assert config.timeout_seconds == 3.5
    assert config.workspace_id == "ws"
    assert config.user_id == "u"
    assert config.agent_id == "agent"
    assert config.session_id == "session-1"


@pytest.mark.anyio
async def test_fusion_memory_client_posts_messages_scope_and_error_flag() -> None:
    seen: dict[str, Any] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(await request.json())
        return web.json_response({"span_ids": ["span-1"]})

    app = web.Application()
    app.router.add_post("/ingest-turn", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    client = _FusionMemoryClient(
        _MemoryClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=2.0,
            workspace_id="ws",
            user_id="u",
            agent_id="agent",
            session_id="session-1",
        )
    )

    try:
        await client.ingest_turn(
            [{"role": "user", "content": "remember my aisle seat preference"}],
            turn_id="turn-1",
            turn_index=1,
            ended_with_error=False,
        )
        assert seen["messages"] == [{"role": "user", "content": "remember my aisle seat preference"}]
        assert seen["scope"]["workspace_id"] == "ws"
        assert seen["scope"]["session_id"] == "session-1"
        assert seen["metadata"]["ended_with_error"] is False
    finally:
        await runner.cleanup()


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
        agent = SessionAgent(ai_socket=ai_socket, tools={})
        user_msg = {"role": "user", "content": "hi"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        all_content = "".join(
            c.choices[0].delta.content or "" for c in chunks if c.choices and c.choices[0].delta.content
        )
        assert "Hello world" in all_content
    finally:
        await mock_server.cleanup()


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
        await request.json()  # verify parsing succeeds
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
        agent = SessionAgent(ai_socket=ai_socket, tools=tools, tool_funcs={"get_weather": _get_weather})

        user_msg = {"role": "user", "content": "What's the weather in Beijing?"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        reasoning = [c.choices[0].delta.reasoning for c in chunks if c.choices and c.choices[0].delta.reasoning]
        assert len(reasoning) > 0, f"No reasoning chunks, got {len(chunks)} total"
        assert any("get_weather" in (r or "") for r in reasoning)

        content = [c.choices[0].delta.content for c in chunks if c.choices and c.choices[0].delta.content]
        assert any("sunny" in (c or "") for c in content)

        assert request_count >= 2
    finally:
        await mock_server.cleanup()


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
        agent = SessionAgent(ai_socket=ai_socket, tools={})
        agent.set_pending_schedule_chunks(
            [
                ChatCompletionChunk(
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(reasoning="[Schedule triggered: daily report]"),
                        )
                    ],
                ),
                ChatCompletionChunk(
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content="Schedule content here"),
                        )
                    ],
                ),
            ]
        )

        user_msg = {"role": "user", "content": "hi"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        reasoning = [c.choices[0].delta.reasoning for c in chunks if c.choices and c.choices[0].delta.reasoning]
        assert any("Schedule triggered" in (r or "") for r in reasoning)

        content = [c.choices[0].delta.content for c in chunks if c.choices and c.choices[0].delta.content]
        assert any("Current response" in (c or "") for c in content)

        # After first run, pending should be cleared
        assert agent._pending_schedule_chunks == []
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_agent_history_accumulation(tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="OK", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(ai_socket=ai_socket, tools={})

        async for _ in agent.run({"role": "user", "content": "first"}):
            pass
        # Should have at least: user message + assistant response
        assert len(agent.history) >= 2

        async for _ in agent.run({"role": "user", "content": "second"}):
            pass
        assert len(agent.history) >= 4
    finally:
        await mock_server.cleanup()


# --- Missing coverage: tool execution error paths ---


async def _make_inline_ai_handler(responses: list[dict]):
    """Create an aiohttp handler that returns predefined responses per request."""
    req_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal req_count
        idx = min(req_count, len(responses) - 1)
        req_count += 1
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {json.dumps(responses[idx])}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    return handler


def _tc(name: str, args: str) -> dict:
    return {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c1", "type": "function", "function": {"name": name, "arguments": args}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


def _stop(content: str) -> dict:
    return {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
    }


@pytest.mark.anyio
async def test_agent_tool_not_registered(tmp_path: Path) -> None:
    handler = await _make_inline_ai_handler([_tc("unknown", "{}"), _stop("done")])
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
        tf = ToolFunction(
            name="unknown", description="X", parameters={"type": "object", "properties": {}, "required": []}
        )
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={"unknown": tf})
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.choices[0].delta.reasoning or "" for c in chunks if c.choices)
        assert "not found" in reasoning.lower()
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_tool_throws_exception_unit(tmp_path: Path) -> None:
    handler = await _make_inline_ai_handler([_tc("crash", "{}"), _stop("recovered")])
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

        async def crash_tool() -> str:
            msg = "BOOM"
            raise RuntimeError(msg)
            return ""

        tf = ToolFunction(
            name="crash", description="X", parameters={"type": "object", "properties": {}, "required": []}
        )
        agent = SessionAgent(
            ai_socket=f"http://127.0.0.1:{port}", tools={"crash": tf}, tool_funcs={"crash": crash_tool}
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.choices[0].delta.reasoning or "" for c in chunks if c.choices)
        assert "BOOM" in reasoning or "RuntimeError" in reasoning
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_tool_returns_int(tmp_path: Path) -> None:
    handler = await _make_inline_ai_handler([_tc("int_tool", "{}"), _stop("done")])
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

        async def int_tool() -> int:
            return 42

        tf = ToolFunction(
            name="int_tool", description="X", parameters={"type": "object", "properties": {}, "required": []}
        )
        agent = SessionAgent(
            ai_socket=f"http://127.0.0.1:{port}", tools={"int_tool": tf}, tool_funcs={"int_tool": int_tool}
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.choices[0].delta.reasoning or "" for c in chunks if c.choices)
        assert "42" in reasoning
    finally:
        await runner.cleanup()


# --- Additional edge case tests ---


@pytest.mark.anyio
async def test_agent_tcp_connector(tmp_path: Path) -> None:
    """Agent should work with http:// TCP URL for ai_socket."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = json.dumps({"id": "t", "choices": [{"delta": {"content": "tcp works"}, "finish_reason": "stop"}]})
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

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
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={})
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        assert "tcp works" in content
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_ai_non_200_response(tmp_path: Path) -> None:
    """AI returning non-200 should yield an error chunk."""

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
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={})
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        assert "Error" in content or "400" in content
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_ai_error_not_in_history(tmp_path: Path) -> None:
    """AI error chunks should not be appended to conversation history."""

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
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={})
        history_len_before = len(agent.history)
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        assert len(chunks) >= 1
        assert len(agent.history) == history_len_before + 1  # only user message added
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_turn_delta_is_flushed_on_stop(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    flushed = anyio.Event()

    class FakeMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen["messages"] = messages
            seen["turn_index"] = turn_index
            seen["ended_with_error"] = ended_with_error
            flushed.set()

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="stored", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(ai_socket=ai_socket, tools={}, memory_client=FakeMemoryClient())
        async for _ in agent.run({"role": "user", "content": "remember my seat preference"}):
            pass

        await flushed.wait()
        assert [item["role"] for item in seen["messages"]] == ["user", "assistant"]
        assert seen["turn_index"] == 1
        assert seen["ended_with_error"] is False
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_turn_delta_is_flushed_on_error(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    flushed = anyio.Event()

    class FakeMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen["messages"] = messages
            seen["turn_index"] = turn_index
            seen["ended_with_error"] = ended_with_error
            flushed.set()

    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=500)

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(ai_socket=ai_socket, tools={}, memory_client=FakeMemoryClient())
        async for _ in agent.run({"role": "user", "content": "danger"}):
            pass

        await flushed.wait()
        assert [item["role"] for item in seen["messages"]] == ["user"]
        assert seen["turn_index"] == 1
        assert seen["ended_with_error"] is True
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_turn_delta_is_flushed_once_on_stream_transport_exception() -> None:
    seen: list[dict[str, Any]] = []
    flushed = anyio.Event()

    class FakeMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen.append(
                {
                    "messages": messages,
                    "turn_index": turn_index,
                    "ended_with_error": ended_with_error,
                }
            )
            flushed.set()

    class TransportFailingAgent(SessionAgent):
        async def _stream_ai_request(self, request_body: dict):
            if False:
                yield
            raise RuntimeError("socket dropped")

    agent = TransportFailingAgent(ai_socket="unused", tools={}, memory_client=FakeMemoryClient())

    with pytest.raises(RuntimeError, match="socket dropped"):
        async for _ in agent.run({"role": "user", "content": "persist me"}):
            pass

    await flushed.wait()
    assert len(seen) == 1
    assert [item["role"] for item in seen[0]["messages"]] == ["user"]
    assert seen[0]["turn_index"] == 1
    assert seen[0]["ended_with_error"] is True


@pytest.mark.anyio
async def test_turn_delta_is_flushed_once_on_run_cancellation() -> None:
    seen: list[dict[str, Any]] = []
    flushed = anyio.Event()
    release_stream = anyio.Event()
    first_chunk_seen = anyio.Event()

    class FakeMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen.append(
                {
                    "messages": messages,
                    "turn_index": turn_index,
                    "ended_with_error": ended_with_error,
                }
            )
            flushed.set()

    class CancellableAgent(SessionAgent):
        async def _stream_ai_request(self, request_body: dict):
            yield ChatCompletionChunk(
                id="chunk-1",
                choices=[StreamChoice(index=0, delta=DeltaMessage(content="partial"))],
            )
            await release_stream.wait()

    agent = CancellableAgent(ai_socket="unused", tools={}, memory_client=FakeMemoryClient())

    async def consume() -> None:
        async for _ in agent.run({"role": "user", "content": "remember cancellation"}):
            first_chunk_seen.set()
            await checkpoint()

    task = asyncio.create_task(consume())
    with pytest.raises(asyncio.CancelledError):
        await first_chunk_seen.wait()
        task.cancel()
        await task

    await flushed.wait()
    assert len(seen) == 1
    assert [item["role"] for item in seen[0]["messages"]] == ["user"]
    assert seen[0]["turn_index"] == 1
    assert seen[0]["ended_with_error"] is True


@pytest.mark.anyio
async def test_turn_delta_flush_is_non_blocking_on_stop(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    started = anyio.Event()
    release = anyio.Event()

    class BlockingMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen["messages"] = messages
            seen["turn_index"] = turn_index
            seen["ended_with_error"] = ended_with_error
            started.set()
            await release.wait()

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="stored", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(ai_socket=ai_socket, tools={}, memory_client=BlockingMemoryClient())
        chunks = None
        with anyio.move_on_after(0.2) as scope:
            chunks = [chunk async for chunk in agent.run({"role": "user", "content": "remember this"})]

        assert scope.cancel_called is False
        assert chunks is not None
        assert any(chunk.choices[0].delta.content == "stored" for chunk in chunks if chunk.choices)

        await started.wait()
        assert [item["role"] for item in seen["messages"]] == ["user", "assistant"]
        assert seen["turn_index"] == 1
        assert seen["ended_with_error"] is False

        release.set()
        await checkpoint()
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_shutdown_drains_pending_memory_flush_tasks() -> None:
    started = anyio.Event()
    release = anyio.Event()
    completed = anyio.Event()

    class BlockingMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            started.set()
            await release.wait()
            completed.set()

    agent = SessionAgent(ai_socket="unused", tools={}, memory_client=BlockingMemoryClient())
    agent.history.append({"role": "user", "content": "queued"})
    agent._schedule_turn_memory_flush(0, turn_index=1, ended_with_error=False)

    await started.wait()
    release.set()
    await agent.shutdown()

    assert completed.is_set()
    assert not agent._memory_flush_tasks


@pytest.mark.anyio
async def test_shutdown_cancels_memory_flush_tasks_after_timeout() -> None:
    cancelled = anyio.Event()

    class HangingMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            try:
                await anyio.sleep_forever()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    agent = SessionAgent(ai_socket="unused", tools={}, memory_client=HangingMemoryClient())
    agent.history.append({"role": "user", "content": "queued"})
    agent._schedule_turn_memory_flush(0, turn_index=1, ended_with_error=False)

    await checkpoint()
    await agent.shutdown(timeout_seconds=0.01)

    assert cancelled.is_set()
    assert not agent._memory_flush_tasks


@pytest.mark.anyio
async def test_turn_delta_is_flushed_on_max_tool_rounds(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    flushed = anyio.Event()

    class FakeMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen["messages"] = messages
            seen["turn_index"] = turn_index
            seen["ended_with_error"] = ended_with_error
            flushed.set()

    async def noop_tool() -> str:
        return "ok"

    handler = await _make_inline_ai_handler([_tc("noop", "{}")])
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
        tf = ToolFunction(
            name="noop",
            description="No-op",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        agent = SessionAgent(
            ai_socket=f"http://127.0.0.1:{port}",
            tools={"noop": tf},
            tool_funcs={"noop": noop_tool},
            memory_client=FakeMemoryClient(),
            max_tool_rounds=1,
        )

        chunks = [chunk async for chunk in agent.run({"role": "user", "content": "loop once"})]

        await flushed.wait()
        content = "".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices)
        assert "[Max tool rounds reached]" in content
        assert [item["role"] for item in seen["messages"]] == ["user", "assistant", "tool"]
        assert seen["turn_index"] == 1
        assert seen["ended_with_error"] is False
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_delayed_flush_keeps_original_turn_delta_when_next_turn_starts(tmp_path: Path) -> None:
    seen: dict[int, list[dict[str, object]]] = {}
    first_flush_started = anyio.Event()
    release_first_flush = anyio.Event()
    both_flushed = anyio.Event()

    class FakeMemoryClient:
        async def ingest_turn(self, messages, *, turn_id, turn_index, ended_with_error):
            seen[turn_index] = list(messages)
            if len(seen) == 2:
                both_flushed.set()

    class DelayedFirstFlushAgent(SessionAgent):
        async def _flush_turn_memory(self, delta, *, turn_index, ended_with_error):
            if turn_index == 1:
                first_flush_started.set()
                await release_first_flush.wait()
            await super()._flush_turn_memory(
                delta,
                turn_index=turn_index,
                ended_with_error=ended_with_error,
            )

    request_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal request_count
        request_count += 1
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content=f"reply-{request_count}", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = DelayedFirstFlushAgent(ai_socket=ai_socket, tools={}, memory_client=FakeMemoryClient())

        async for _ in agent.run({"role": "user", "content": "first"}):
            pass

        await first_flush_started.wait()

        async for _ in agent.run({"role": "user", "content": "second"}):
            pass

        release_first_flush.set()
        await both_flushed.wait()

        assert [item["content"] for item in seen[1]] == ["first", "reply-1"]
        assert [item["content"] for item in seen[2]] == ["second", "reply-2"]
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_session_run_shuts_down_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events: list[str] = []

    class FakeAgent:
        schedules: ClassVar[list[object]] = []

        async def handle_chat_completions(self, request):
            raise AssertionError("not expected")

        async def shutdown(self, timeout_seconds: float = 5.0) -> None:
            events.append(f"shutdown:{timeout_seconds}")

    fake_agent = FakeAgent()

    async def fake_create(**kwargs):
        return fake_agent

    async def fake_serve_session(*, channel_socket, handler, lock):
        events.append("serve")
        await anyio.sleep_forever()

    monkeypatch.setattr("psi_agent.session.SessionAgent.create", fake_create)
    monkeypatch.setattr("psi_agent.session.serve_session", fake_serve_session)

    session = Session(
        workspace=str(tmp_path),
        channel_socket=str(tmp_path / "channel.sock"),
        ai_socket=str(tmp_path / "ai.sock"),
    )
    with anyio.move_on_after(0.1) as scope:
        await session.run()

    assert scope.cancel_called
    assert events == ["serve", "shutdown:5.0"]


@pytest.mark.anyio
async def test_agent_non_data_sse_line(tmp_path: Path) -> None:
    """SSE lines not starting with 'data: ' should be skipped."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b":comment\n")
        await resp.write(b"event: ping\ndata: {}\n\n")
        await resp.write(
            b"data: "
            + json.dumps(
                {"id": "t", "choices": [{"delta": {"content": "after event"}, "finish_reason": "stop"}]}
            ).encode()
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
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={})
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        assert "after event" in content
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_empty_content_stop(tmp_path: Path) -> None:
    """AI returning stop with no content should not crash."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(
            b"data: " + json.dumps({"id": "t", "choices": [{"delta": {}, "finish_reason": "stop"}]}).encode() + b"\n\n"
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
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={})
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        assert len(chunks) >= 1
    finally:
        await runner.cleanup()


# --- History persistence tests ---


@pytest.mark.anyio
async def test_load_history_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    history = await _load_history(path)
    assert history == []


@pytest.mark.anyio
async def test_load_history_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    path.parent.mkdir()
    path.write_text('{"role": "user", "content": "hi"}\n{"role": "assistant", "content": "hello"}\n')
    history = await _load_history(path)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "hi"}


@pytest.mark.anyio
async def test_load_history_corrupt_line_skipped(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    path.parent.mkdir()
    path.write_text('{"role": "user", "content": "hi"}\nnot valid json\n{"role": "assistant", "content": "ok"}\n')
    history = await _load_history(path)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


@pytest.mark.anyio
async def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    path.parent.mkdir()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    await _save_history(path, msgs)
    loaded = await _load_history(path)
    assert loaded == msgs


@pytest.mark.anyio
async def test_history_saved_after_stop(tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = json.dumps({"id": "t", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

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
        history_path = tmp_path / "histories" / "s.jsonl"
        history_path.parent.mkdir()

        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={}, history_path=history_path)
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        assert "ok" in content

        assert history_path.exists()
        loaded = await _load_history(history_path)
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[1]["role"] == "assistant"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_history_not_saved_on_error(tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(status=500)

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
        history_path = tmp_path / "histories" / "s.jsonl"
        history_path.parent.mkdir()
        history_path.write_text('{"role": "system", "content": "original"}\n')

        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}", tools={}, history_path=history_path)
        async for _ in agent.run({"role": "user", "content": "hi"}):
            pass

        loaded = await _load_history(history_path)
        assert len(loaded) == 1
        assert loaded[0]["content"] == "original"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_histories_dir_and_gitignore_created(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tools").mkdir()
    (workspace / "schedules").mkdir()

    histories_dir = workspace / "histories"

    agent = await SessionAgent.create(ai_socket="http://x", workspace_path=workspace, session_id="test")
    assert histories_dir.is_dir()
    assert (histories_dir / ".gitignore").read_text() == "*\n"
    assert agent._history_path is not None
