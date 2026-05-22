from __future__ import annotations

import json
import socket as _s
import textwrap
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction
from psi_agent.session.agent import SessionAgent
from psi_agent.session.tools import load_tools_from_workspace


async def _get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Weather in {city}: sunny, 22 C"


def _sse_chunk(content: str = "", reasoning: str = "", finish: str | None = None) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
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
        self._app.router.add_post("/v1/chat/completions", handler)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.UnixSite(self._runner, str(self.socket_path))
        await site.start()
        return str(self.socket_path)

    async def cleanup(self) -> None:
        if self._runner:
            await self._runner.cleanup()


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
        agent = SessionAgent(ai_socket=ai_socket, tools={}, model="test")
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

    tools = await load_tools_from_workspace(tools_dir)

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
        agent = SessionAgent(ai_socket=ai_socket, tools=tools, model="test")
        agent.register_tool_func("get_weather", _get_weather)

        user_msg = {"role": "user", "content": "What's the weather in Beijing?"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        reasoning = [
            c.choices[0].delta.reasoning_content for c in chunks if c.choices and c.choices[0].delta.reasoning_content
        ]
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
        agent = SessionAgent(ai_socket=ai_socket, tools={}, model="test")
        agent.set_pending_schedule_chunks(
            [
                ChatCompletionChunk(
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(reasoning_content="[Schedule triggered: daily report]"),
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

        reasoning = [
            c.choices[0].delta.reasoning_content for c in chunks if c.choices and c.choices[0].delta.reasoning_content
        ]
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
        agent = SessionAgent(ai_socket=ai_socket, tools={}, model="test")

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
    app.router.add_post("/v1/chat/completions", handler)
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
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={"unknown": tf}, model="test")
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.choices[0].delta.reasoning_content or "" for c in chunks if c.choices)
        assert "not found" in reasoning.lower()
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_tool_throws_exception_unit(tmp_path: Path) -> None:
    handler = await _make_inline_ai_handler([_tc("crash", "{}"), _stop("recovered")])
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
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
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={"crash": tf}, model="test")
        agent.register_tool_func("crash", crash_tool)
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.choices[0].delta.reasoning_content or "" for c in chunks if c.choices)
        assert "BOOM" in reasoning or "RuntimeError" in reasoning
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_tool_returns_int(tmp_path: Path) -> None:
    handler = await _make_inline_ai_handler([_tc("int_tool", "{}"), _stop("done")])
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
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
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={"int_tool": tf}, model="test")
        agent.register_tool_func("int_tool", int_tool)
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.choices[0].delta.reasoning_content or "" for c in chunks if c.choices)
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
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
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
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
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
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
        history_len_before = len(agent.history)
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        assert len(chunks) >= 1
        assert len(agent.history) == history_len_before + 1  # only user message added
    finally:
        await runner.cleanup()


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
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
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
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        assert len(chunks) >= 1
    finally:
        await runner.cleanup()
