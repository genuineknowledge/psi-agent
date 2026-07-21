from __future__ import annotations

import json
import socket as _s
import textwrap
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.protocol import AgentChunk, AgentError
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry


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
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=ToolRegistry())
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
    await anyio.Path(tools_dir).mkdir()
    await anyio.Path(tools_dir / "get_weather.py").write_text(
        textwrap.dedent("""\
        async def get_weather(city: str) -> str:
            \"\"\"Get weather for a city.

            Args:
                city: The city name.
            \"\"\"
            return f"Weather in {city}: sunny, 22 C"
    """),
        encoding="utf-8",
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
            tool_registry=tr,
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
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=ToolRegistry())
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
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=ToolRegistry())

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
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(files={"__test__": FileEntry(file_hash="", tools={"unknown": tf}, funcs={})}),
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
            tool_registry=ToolRegistry(
                files={"__test__": FileEntry(file_hash="", tools={"crash": tf}, funcs={"crash": crash_tool})}
            ),
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
            tool_registry=ToolRegistry(
                files={"__test__": FileEntry(file_hash="", tools={"int_tool": tf}, funcs={"int_tool": int_tool})}
            ),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "t"})]
        reasoning = "".join(c.reasoning or "" for c in chunks)
        assert "42" in reasoning
    finally:
        await runner.cleanup()


async def _run_tool_capturing_user_key(
    tmp_path: Path, tool_args: str, tool_func: Any, has_user_key_param: bool, extra_params: dict | None
) -> None:
    """Drive one tool call and return what the tool received. tool_func records into `seen`."""
    handler = await _make_inline_ai_handler([_tc("probe", tool_args), _stop("done")])
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
        props = {"user_key": {"type": "string"}} if has_user_key_param else {}
        tf = ToolFunction(
            name="probe", description="X", parameters={"type": "object", "properties": props, "required": []}
        )
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(
                files={"__test__": FileEntry(file_hash="", tools={"probe": tf}, funcs={"probe": tool_func})}
            ),
        )
        async for _ in agent.run({"role": "user", "content": "t"}, extra_params):
            pass
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_injects_sender_as_user_key(tmp_path: Path) -> None:
    """When a tool declares user_key and the LLM omits it, the sender open_id is injected."""
    seen: dict[str, Any] = {}

    async def probe(user_key: str = "") -> str:
        seen["user_key"] = user_key
        return "ok"

    await _run_tool_capturing_user_key(tmp_path, "{}", probe, True, {"x_feishu_sender_open_id": "ou_sender"})
    assert seen["user_key"] == "ou_sender"


@pytest.mark.anyio
async def test_agent_does_not_override_explicit_user_key(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def probe(user_key: str = "") -> str:
        seen["user_key"] = user_key
        return "ok"

    await _run_tool_capturing_user_key(
        tmp_path, '{"user_key": "ou_explicit"}', probe, True, {"x_feishu_sender_open_id": "ou_sender"}
    )
    assert seen["user_key"] == "ou_explicit"


@pytest.mark.anyio
async def test_agent_no_inject_when_tool_lacks_user_key(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def probe() -> str:
        seen["called"] = True
        return "ok"

    # tool has no user_key param → must not crash by injecting an unexpected kwarg
    await _run_tool_capturing_user_key(tmp_path, "{}", probe, False, {"x_feishu_sender_open_id": "ou_sender"})
    assert seen.get("called") is True


@pytest.mark.anyio
async def test_agent_no_inject_without_sender(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def probe(user_key: str = "") -> str:
        seen["user_key"] = user_key
        return "ok"

    # no sender in extra_params → user_key stays empty (tenant behavior)
    await _run_tool_capturing_user_key(tmp_path, "{}", probe, True, None)
    assert seen["user_key"] == ""


async def _run_simple(agent: SessionAgent, content: str, extra: dict | None) -> None:
    async for _ in agent.run({"role": "user", "content": content}, extra):
        pass


@pytest.mark.anyio
async def test_agent_conversation_key_isolates_history(tmp_path: Path) -> None:
    """Different conversation keys → separate histories; same key accumulates."""
    handler = await _make_inline_ai_handler([_stop("a1"), _stop("b1"), _stop("a2"), _stop("nokey")])
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
        (tmp_path / "histories").mkdir(exist_ok=True)
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), workspace_path=tmp_path)

        await _run_simple(agent, "hello A", {"x_conversation_key": "oc_chatA"})
        await _run_simple(agent, "hello B", {"x_conversation_key": "oc_chatB"})
        await _run_simple(agent, "more A", {"x_conversation_key": "oc_chatA"})

        conv_a = agent._conversations["oc_chatA"]
        conv_b = agent._conversations["oc_chatB"]
        assert conv_a is not conv_b
        a_texts = [str(m.get("content", "")) for m in conv_a.messages]
        b_texts = [str(m.get("content", "")) for m in conv_b.messages]
        # A has its two turns, not B's
        assert any("hello A" in t for t in a_texts) and any("more A" in t for t in a_texts)
        assert not any("hello B" in t for t in a_texts)
        assert any("hello B" in t for t in b_texts)
        assert not any("hello A" in t for t in b_texts)
        # each chat has its own history file
        assert (tmp_path / "histories" / "oc_chatA.jsonl").exists()
        assert (tmp_path / "histories" / "oc_chatB.jsonl").exists()

        # no key → default conversation, not mixed with the per-chat ones
        await _run_simple(agent, "nokey msg", None)
        assert any("nokey msg" in str(m.get("content", "")) for m in agent._conversation.messages)
        assert not any("nokey msg" in str(m.get("content", "")) for m in conv_a.messages)
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry())
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry())
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry())
        history_len_before = len(agent._conversation.messages)
        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass
        assert len(agent._conversation.messages) == history_len_before + 2
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry())
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry())
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
    await anyio.Path(path.parent).mkdir()
    await anyio.Path(path).write_text(
        '{"role": "user", "content": "hi"}\n{"role": "assistant", "content": "hello"}\n', encoding="utf-8"
    )
    history = await Conversation._load(path)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "hi"}


@pytest.mark.anyio
async def test_load_history_corrupt_line_skipped(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    await anyio.Path(path.parent).mkdir()
    await anyio.Path(path).write_text(
        '{"role": "user", "content": "hi"}\nnot valid json\n{"role": "assistant", "content": "ok"}\n', encoding="utf-8"
    )
    history = await Conversation._load(path)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


@pytest.mark.anyio
async def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "histories" / "session.jsonl"
    await anyio.Path(path.parent).mkdir()
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
        await anyio.Path(history_path.parent).mkdir()

        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=history_path),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        content = "".join(c.content or "" for c in chunks)
        assert "ok" in content

        assert await anyio.Path(history_path).exists()
        loaded = await Conversation._load(history_path)
        assert len(loaded) == 3
        assert loaded[0]["role"] == "system"
        assert loaded[1]["role"] == "user"
        assert loaded[2]["role"] == "assistant"
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
        await anyio.Path(history_path.parent).mkdir()
        await anyio.Path(history_path).write_text('{"role": "system", "content": "original"}\n', encoding="utf-8")

        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=history_path),
        )
        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass

        loaded = await Conversation._load(history_path)
        assert len(loaded) == 2
        assert loaded[0]["role"] == "system"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_histories_dir_and_gitignore_created(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    await anyio.Path(workspace).mkdir()
    await anyio.Path(workspace / "tools").mkdir()
    await anyio.Path(workspace / "schedules").mkdir()

    histories_dir = workspace / "histories"

    agent = await SessionAgent.create(ai_socket="http://x", workspace_path=workspace, session_id="test")
    assert await anyio.Path(histories_dir).is_dir()
    assert await anyio.Path(histories_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert agent._conversation._path is not None


# --- Snapshot / rollback tests ---


class TestConversationSnapshot:
    @pytest.mark.anyio
    async def test_add_auto_snapshots_and_rollback_restores(self) -> None:
        conv = Conversation(messages=[{"role": "system", "content": "sys"}])
        conv.add({"role": "user", "content": "hi"})
        assert len(conv.messages) == 2
        conv.rollback()
        assert len(conv.messages) == 1
        assert conv.messages[0] == {"role": "system", "content": "sys"}

    @pytest.mark.anyio
    async def test_rollback_restores_pending(self) -> None:
        conv = Conversation()
        conv.stash([AgentChunk(content="hello")])
        conv.add({"role": "user", "content": "hi"})
        conv.clear_pending()
        assert conv._pending == []
        conv.rollback()
        assert conv._pending == [AgentChunk(content="hello")]
        assert conv.messages == []

    @pytest.mark.anyio
    async def test_rollback_idempotent_without_snapshot(self) -> None:
        conv = Conversation(messages=[{"role": "user", "content": "q"}])
        conv.rollback()
        assert len(conv.messages) == 1

    @pytest.mark.anyio
    async def test_commit_clears_snapshot(self) -> None:
        conv = Conversation(messages=[{"role": "system", "content": "s1"}], path=None)
        conv.add({"role": "user", "content": "u1"})
        await conv.commit()
        conv.rollback()
        assert len(conv.messages) == 2

    @pytest.mark.anyio
    async def test_commit_then_next_add_creates_new_snapshot(self) -> None:
        conv = Conversation(messages=[{"role": "system", "content": "s1"}])
        conv.add({"role": "user", "content": "u1"})
        await conv.commit()
        conv.add({"role": "user", "content": "u2"})
        conv.rollback()
        assert len(conv.messages) == 2
        assert conv.messages[1] == {"role": "user", "content": "u1"}


class TestPeekPendingSafety:
    @pytest.mark.anyio
    async def test_peek_pending_does_not_clear(self) -> None:
        conv = Conversation()
        conv.stash([AgentChunk(content="a"), AgentChunk(content="b")])
        result = conv.peek_pending()
        assert len(result) == 2
        assert len(conv._pending) == 2
        assert conv._pending == [AgentChunk(content="a"), AgentChunk(content="b")]

    @pytest.mark.anyio
    async def test_clear_pending_drops_all(self) -> None:
        conv = Conversation()
        conv.stash([AgentChunk(content="x")])
        conv.clear_pending()
        assert conv._pending == []


# --- Agent snapshot / rollback integration tests ---


@pytest.mark.anyio
async def test_agent_rollback_restores_history_on_error(tmp_path: Path) -> None:
    """AI error should rollback the conversation to before the turn."""
    history_path = tmp_path / "histories" / "s.jsonl"
    await anyio.Path(history_path.parent).mkdir()

    conv = Conversation(
        messages=[{"role": "system", "content": "original"}],
        path=history_path,
    )
    await conv.save()

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
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(),
            conversation=conv,
        )
        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass

        assert len(agent._conversation.messages) == 2
        assert agent._conversation.messages[0] == {"role": "system", "content": "original"}

        loaded = await Conversation._load(history_path)
        assert len(loaded) == 2
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_rollback_restores_pending_on_error(tmp_path: Path) -> None:
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
        agent = SessionAgent(ai_client=AiClient(f"http://127.0.0.1:{port}"), tool_registry=ToolRegistry())
        agent.set_pending_schedule_chunks([AgentChunk(reasoning="schedule output")])

        with pytest.raises(AgentError):
            async for _ in agent.run({"role": "user", "content": "hi"}):
                pass

        assert len(agent._conversation._pending) == 0
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_saves_on_max_tool_rounds(tmp_path: Path) -> None:
    history_path = tmp_path / "histories" / "s.jsonl"
    await anyio.Path(history_path.parent).mkdir()

    def _tc_factory(name: str) -> dict:
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
                            {
                                "index": 0,
                                "id": "c1",
                                "type": "function",
                                "function": {"name": name, "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        tc = _tc_factory("unknown")
        await resp.write(f"data: {json.dumps(tc)}\n\n".encode())
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
        tf = ToolFunction(
            name="unknown", description="X", parameters={"type": "object", "properties": {}, "required": []}
        )
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(files={"__test__": FileEntry(file_hash="", tools={"unknown": tf}, funcs={})}),
            conversation=Conversation(path=history_path),
            max_tool_rounds=1,
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]

        content = "".join(c.content or "" for c in chunks)
        assert "Max tool rounds reached" in content

        loaded = await Conversation._load(history_path)
        assert any(m.get("content") == "[Max tool rounds reached]" for m in loaded)
    finally:
        await runner.cleanup()
