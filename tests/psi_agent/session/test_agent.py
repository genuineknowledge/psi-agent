from __future__ import annotations

import json
import socket as _s
import textwrap
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.session._conversation import Conversation
from psi_agent.session._tool_registry import ToolRegistry
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.protocol import AgentChunk, AgentError, ToolFunction


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
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=ToolRegistry(tools={}))
        user_msg = {"role": "user", "content": "hi"}
        chunks = []
        async for chunk in agent.run(user_msg):
            chunks.append(chunk)

        all_content = "".join(c.content or "" for c in chunks)
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

    tr = await ToolRegistry.load(tools_dir)

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
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(tools=tr.tools, funcs={"get_weather": _get_weather}),
        )

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
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=ToolRegistry(tools={}))
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

        assert agent._conversation._pending == []
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
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=ToolRegistry(tools={}))

        async for _ in agent.run({"role": "user", "content": "first"}):
            pass
        assert len(agent._conversation.messages) >= 2

        async for _ in agent.run({"role": "user", "content": "second"}):
            pass
        assert len(agent._conversation.messages) >= 4
    finally:
        await mock_server.cleanup()


# --- Missing coverage: tool execution error paths ---


async def _make_inline_ai_handler(responses: list[dict]):
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
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry(tools={"unknown": tf})
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.reasoning or "" for c in chunks)
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
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(tools={"crash": tf}, funcs={"crash": crash_tool}),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.reasoning or "" for c in chunks)
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
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(tools={"int_tool": tf}, funcs={"int_tool": int_tool}),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.reasoning or "" for c in chunks)
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry(tools={}))
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.content or "" for c in chunks)
        assert "tcp works" in content
    finally:
        await runner.cleanup()


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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry(tools={}))
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry(tools={}))
        history_len_before = len(agent._conversation.messages)
        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass
        assert len(agent._conversation.messages) == history_len_before + 1  # only user message added
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
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry(tools={}))
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.content or "" for c in chunks)
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry(tools={}))
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        assert isinstance(chunks, list)  # should not crash
    finally:
        await runner.cleanup()


# --- History persistence tests ---


@pytest.mark.anyio
async def test_load_history_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    history = await Conversation._load(path)
    assert history == []


@pytest.mark.anyio
async def test_load_history_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    path.parent.mkdir()
    path.write_text('{"role": "user", "content": "hi"}\n{"role": "assistant", "content": "hello"}\n')
    history = await Conversation._load(path)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "hi"}


@pytest.mark.anyio
async def test_load_history_corrupt_line_skipped(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    path.parent.mkdir()
    path.write_text('{"role": "user", "content": "hi"}\nnot valid json\n{"role": "assistant", "content": "ok"}\n')
    history = await Conversation._load(path)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


@pytest.mark.anyio
async def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    path.parent.mkdir()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    conv = Conversation(messages=msgs, path=path)
    await conv.save()
    loaded = await Conversation._load(path)
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

        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(tools={}),
            conversation=Conversation(path=history_path),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.content or "" for c in chunks)
        assert "ok" in content

        assert history_path.exists()
        loaded = await Conversation._load(history_path)
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

        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(tools={}),
            conversation=Conversation(path=history_path),
        )
        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass

        loaded = await Conversation._load(history_path)
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
    assert agent._conversation._path is not None
