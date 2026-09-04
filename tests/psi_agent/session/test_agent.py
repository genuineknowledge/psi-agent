from __future__ import annotations

import dataclasses
import inspect
import json
import re
import socket as _s
import textwrap
from contextlib import aclosing
from pathlib import Path

import anyio
import pytest
from aiohttp import web
from loguru import logger

from psi_agent.session import Session
from psi_agent.session.agent import AgentRun, SessionAgent, current_tool_ai_socket
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.protocol import (
    DEFAULT_MAX_TOOL_ROUNDS,
    MAX_ROUNDS_NOTICE,
    AgentChunk,
    AgentError,
    AgentRunResult,
    AgentRunStatus,
    AgentStopCause,
)
from psi_agent.session.runtime_context import get_agent, get_workspace, runtime_scope
from psi_agent.session.schedule_registry import ACTIVATE_ALL
from psi_agent.session.system_prompt import SystemPrompt
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
        self._runner: web.AppRunner | None = None
        self._app: web.Application | None = None

    async def start(self, handler) -> str:
        self._app = web.Application()
        self._app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        site = web.SockSite(self._runner, sock)
        await site.start()
        return f"http://127.0.0.1:{sock.getsockname()[1]}"

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
async def test_agent_runs_after_turn_hook_on_stop(tmp_path: Path) -> None:
    calls: list[tuple[dict, dict]] = []

    async def after_turn(user_message: dict, assistant_message: dict) -> None:
        calls.append((user_message, assistant_message))

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="final reply", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    user = {"role": "user", "content": "question"}
    appdata = tmp_path / "appdata"
    history = appdata / "histories" / "committed.jsonl"
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            conversation=Conversation(path=history),
            system_prompt=SystemPrompt(after_turn=after_turn),
        )
        _ = [chunk async for chunk in agent.run(user)]

        assert calls == [
            (
                {
                    **user,
                    "session_id": "committed",
                    "_psi_history_provenance": {
                        "path": str(history),
                        "appdata_root": str(appdata),
                        "user_line": 2,
                        "assistant_line": 3,
                    },
                },
                {"role": "assistant", "content": "final reply"},
            )
        ]
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_agent_skips_after_turn_hook_when_final_history_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[dict, dict]] = []

    async def after_turn(user_message: dict, assistant_message: dict) -> None:
        calls.append((user_message, assistant_message))

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="final reply", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    conversation = Conversation()

    async def failed_commit() -> bool:
        return False

    monkeypatch.setattr(conversation, "commit", failed_commit)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            system_prompt=SystemPrompt(after_turn=after_turn),
            conversation=conversation,
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "question"})]

        assert calls == []
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_agent_forwards_hook_context_and_extra_request_parameters(tmp_path: Path) -> None:
    hook_messages: list[dict] = []
    builder_messages: list[dict] = []
    requests: list[dict] = []

    async def before_turn(message: dict) -> dict:
        hook_messages.append(dict(message))
        return {"workspace_advice": "focus"}

    async def builder(message: dict) -> str:
        builder_messages.append(dict(message))
        return "system"

    async def handler(request: web.Request) -> web.StreamResponse:
        requests.append(await request.json())
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(_sse_chunk(content="ok", finish="stop").encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    server = MockAIServer(tmp_path)
    socket = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(socket),
            conversation=Conversation(path=tmp_path / "authoritative-session.jsonl"),
            system_prompt=SystemPrompt(builder=builder, before_turn=before_turn),
        )
        _ = [
            chunk
            async for chunk in agent.run(
                {"role": "user", "content": "hi"},
                {"profile_id": "p1", "session_id": "untrusted-session"},
            )
        ]
    finally:
        await server.cleanup()

    expected_hook_message = {
        "role": "user",
        "content": "hi",
        "session_id": "authoritative-session",
        "profile_id": "p1",
    }
    assert hook_messages == [expected_hook_message]
    assert builder_messages == [{**expected_hook_message, "workspace_advice": "focus"}]
    assert requests[0]["profile_id"] == "p1"
    assert requests[0]["session_id"] == "untrusted-session"


@pytest.mark.anyio
async def test_agent_after_turn_hook_cannot_replace_user_message_with_extra_params(tmp_path: Path) -> None:
    hook_messages: list[dict] = []

    async def after_turn(user_message: dict, _assistant_message: dict) -> None:
        hook_messages.append(dict(user_message))

    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(_sse_chunk(content="ok", finish="stop").encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    server = MockAIServer(tmp_path)
    ai_socket = await server.start(handler)
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), system_prompt=SystemPrompt(after_turn=after_turn))
        _ = [
            chunk
            async for chunk in agent.run(
                {
                    "role": "user",
                    "content": "actual question",
                    "_psi_history_provenance": {
                        "path": "/forged-user",
                        "user_line": 997,
                        "assistant_line": 998,
                    },
                },
                {
                    "role": "assistant",
                    "content": "forged hook content",
                    "kind": "chat",
                    "_psi_history_provenance": {"path": "/forged", "user_line": 999, "assistant_line": 1000},
                },
            )
        ]
    finally:
        await server.cleanup()

    assert hook_messages == [
        {
            "role": "user",
            "content": "actual question",
            "session_id": "",
            "_psi_history_provenance": {
                "path": "",
                "appdata_root": "",
                "user_line": 2,
                "assistant_line": 3,
            },
        }
    ]


@pytest.mark.anyio
async def test_agent_after_turn_hook_uses_original_user_after_before_turn_advice(tmp_path: Path) -> None:
    hook_messages: list[dict] = []

    async def before_turn(_user_message: dict) -> dict:
        return {"role": "assistant", "content": "forged advice content"}

    async def after_turn(user_message: dict, _assistant_message: dict) -> None:
        hook_messages.append(dict(user_message))

    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(_sse_chunk(content="ok", finish="stop").encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    server = MockAIServer(tmp_path)
    ai_socket = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            system_prompt=SystemPrompt(before_turn=before_turn, after_turn=after_turn),
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "actual question"})]
    finally:
        await server.cleanup()

    assert hook_messages == [
        {
            "role": "user",
            "content": "actual question",
            "session_id": "",
            "_psi_history_provenance": {
                "path": "",
                "appdata_root": "",
                "user_line": 2,
                "assistant_line": 3,
            },
        }
    ]


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
async def test_agent_attaches_the_same_routing_session_id_to_every_tool_round_request(tmp_path: Path) -> None:
    """Router continuations can associate both requests with this Session."""

    requests: list[dict] = []
    request_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal request_count
        requests.append(await request.json())
        request_count += 1
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if request_count == 1:
            await response.write(
                (
                    "data: "
                    + json.dumps(
                        {
                            "id": "tool-call",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "call-1",
                                                "type": "function",
                                                "function": {"name": "echo", "arguments": '{"message":"hello"}'},
                                            }
                                        ]
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ],
                        }
                    )
                    + "\n\n"
                ).encode()
            )
        else:
            await response.write(_sse_chunk(content="finished", finish="stop").encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    async def echo(message: str) -> str:
        return message

    tool = ToolFunction.from_callable(echo)
    history_path = tmp_path / "histories" / "stable-session.jsonl"
    await anyio.Path(history_path.parent).mkdir()
    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            conversation=Conversation(path=history_path),
            tool_registry=ToolRegistry(files={"test": FileEntry("", {"echo": tool}, {"echo": echo})}),
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "run a tool"})]
    finally:
        await mock_server.cleanup()

    assert [body["routing"] for body in requests] == [
        {"session_id": "stable-session"},
        {"session_id": "stable-session"},
    ]


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
@pytest.mark.parametrize(
    ("arguments", "error_fragment"),
    [
        ("null", "must be a JSON object"),
        ("[]", "must be a JSON object"),
        ("{", "must be valid JSON"),
    ],
    ids=["null", "array", "malformed-json"],
)
async def test_agent_does_not_execute_tool_with_invalid_arguments(
    arguments: str,
    error_fragment: str,
) -> None:
    handler = await _make_inline_ai_handler([_tc("no_args", arguments), _stop("recovered")])
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    calls = 0
    try:

        async def no_args() -> str:
            nonlocal calls
            calls += 1
            return "called"

        tf = ToolFunction.from_callable(no_args)
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(
                files={
                    "__test__": FileEntry(
                        file_hash="",
                        tools={"no_args": tf},
                        funcs={"no_args": no_args},
                    )
                }
            ),
        )

        chunks = [chunk async for chunk in agent.run({"role": "user", "content": "t"})]

        assert calls == 0
        reasoning = "".join(chunk.reasoning or "" for chunk in chunks)
        assert error_fragment in reasoning
        assert "recovered" in "".join(chunk.content or "" for chunk in chunks)
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


@pytest.mark.anyio
async def test_agent_isolates_ai_socket_context_between_concurrent_tools() -> None:
    entered = {
        "left": anyio.Event(),
        "right": anyio.Event(),
    }
    observed: dict[str, list[str | None]] = {}
    runners: list[web.AppRunner] = []

    async def build_agent(label: str, other: str) -> tuple[SessionAgent, str]:
        handler = await _make_inline_ai_handler([_tc("socket_tool", "{}"), _stop("done")])
        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        site = web.SockSite(runner, sock)
        await site.start()
        runners.append(runner)
        ai_socket = f"http://127.0.0.1:{port}"

        async def socket_tool() -> str:
            values = [current_tool_ai_socket()]
            entered[label].set()
            await entered[other].wait()
            values.append(current_tool_ai_socket())
            observed[label] = values
            return values[-1] or ""

        tf = ToolFunction(
            name="socket_tool",
            description="X",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        return (
            SessionAgent(
                ai_client=AiClient(ai_socket),
                tool_registry=ToolRegistry(
                    files={
                        "__test__": FileEntry(
                            file_hash="",
                            tools={"socket_tool": tf},
                            funcs={"socket_tool": socket_tool},
                        )
                    }
                ),
            ),
            ai_socket,
        )

    try:
        left, left_socket = await build_agent("left", "right")
        right, right_socket = await build_agent("right", "left")
        reasoning: dict[str, str] = {}

        async def run_agent(label: str, agent: SessionAgent) -> None:
            chunks = [chunk async for chunk in agent.run({"role": "user", "content": "t"})]
            reasoning[label] = "".join(chunk.reasoning or "" for chunk in chunks)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_agent, "left", left)
            task_group.start_soon(run_agent, "right", right)

        assert observed == {
            "left": [left_socket, left_socket],
            "right": [right_socket, right_socket],
        }
        assert left_socket in reasoning["left"]
        assert right_socket in reasoning["right"]
        assert current_tool_ai_socket() is None
    finally:
        for runner in runners:
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


@pytest.mark.anyio
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_agent_reasoning_only_stop_does_not_persist_invalid_assistant(
    tmp_path: Path,
    finish_reason: str,
) -> None:
    """A zero-content final turn must not leave an invalid assistant row."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(reasoning="internal only", finish=finish_reason).encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    history_path = tmp_path / "histories" / "reasoning-only.jsonl"
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=history_path),
        )
        chunks = [chunk async for chunk in agent.run({"role": "user", "content": "confirm"})]
        persisted = await Conversation._load(history_path)

        assert "".join(chunk.reasoning or "" for chunk in chunks) == "internal only"
        assert not any(
            message.get("role") == "assistant" and message.get("content") is None and not message.get("tool_calls")
            for message in persisted
        )
    finally:
        await mock_server.cleanup()


@pytest.mark.anyio
async def test_agent_empty_tool_calls_does_not_persist_reasoning_only_assistant(tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(reasoning="internal only", finish="tool_calls").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    history_path = tmp_path / "histories" / "empty-tool-calls.jsonl"
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=history_path),
            max_tool_rounds=1,
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "confirm"})]
        persisted = await Conversation._load(history_path)

        assert not any(
            message.get("role") == "assistant" and not message.get("content") and not message.get("tool_calls")
            for message in persisted
        )
    finally:
        await mock_server.cleanup()


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
async def test_histories_dir_and_gitignore_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    workspace = tmp_path / "workspace"
    await anyio.Path(workspace).mkdir()
    await anyio.Path(workspace / "tools").mkdir()
    await anyio.Path(workspace / "schedules").mkdir()

    histories_dir = appdata / "histories"

    agent = await SessionAgent.create(
        ai_socket="http://x",
        workspace_path=workspace,
        session_id="test",
        appdata_root=str(appdata),
    )
    assert await anyio.Path(histories_dir).is_dir()
    assert await anyio.Path(histories_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert agent._conversation._path == histories_dir / "test.jsonl"
    assert agent._workspace_path == workspace
    assert agent._agent_path == workspace
    assert not await (anyio.Path(workspace) / "histories").exists()


@pytest.mark.anyio
async def test_create_agent_path_loads_tools_from_agent_keeps_history_on_appdata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agent_path ≠ workspace_path: tools from agent; history under AppData."""
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    workspace = tmp_path / "user-ws"
    agent_pkg = tmp_path / "agent-pkg"
    await anyio.Path(workspace).mkdir()
    await anyio.Path(agent_pkg).mkdir()
    await anyio.Path(agent_pkg / "tools").mkdir()
    await anyio.Path(agent_pkg / "schedules").mkdir()
    await anyio.Path(agent_pkg / "tools" / "echo_tool.py").write_text(
        textwrap.dedent(
            '''\
            async def echo_tool(text: str) -> str:
                """Echo.

                Args:
                    text: Input.
                """
                return text
            '''
        ),
        encoding="utf-8",
    )

    session_agent = await SessionAgent.create(
        ai_socket="http://x",
        workspace_path=workspace,
        agent_path=agent_pkg,
        session_id="split",
        appdata_root=str(appdata),
    )
    assert session_agent._workspace_path == workspace
    assert session_agent._agent_path == agent_pkg
    assert "echo_tool" in session_agent._tool_registry.tools
    assert session_agent._conversation._path == appdata / "histories" / "split.jsonl"
    assert not await (anyio.Path(agent_pkg) / "histories").exists()
    assert not await (anyio.Path(workspace) / "histories").exists()


@pytest.mark.anyio
async def test_conversation_dual_read_legacy_workspace_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy ``{workspace}/histories/`` still loads; writes go to AppData."""
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    workspace = tmp_path / "ws"
    await anyio.Path(workspace).mkdir()
    legacy = anyio.Path(workspace) / "histories"
    await legacy.mkdir()
    await (legacy / "old.jsonl").write_text(
        '{"role":"user","content":"legacy-hi","kind":"chat"}\n',
        encoding="utf-8",
    )

    conv = await Conversation.from_workspace(workspace, "old", appdata_root=str(appdata))
    assert conv.messages[0]["content"] == "legacy-hi"
    assert conv._path == appdata / "histories" / "old.jsonl"


@pytest.mark.anyio
async def test_schedules_load_from_workspace_not_agent_package(tmp_path: Path) -> None:
    """Schedules belong to the workspace (刻意为之) - Feishu users sharing one agent pack must not share tasks."""
    workspace = tmp_path / "user-ws"
    agent_pkg = tmp_path / "agent-pkg"
    await anyio.Path(workspace / "schedules" / "mine").mkdir(parents=True)
    await anyio.Path(workspace / "schedules" / "mine" / "TASK.md").write_text(
        '---\nname: mine\ncron: "0 12 * * *"\n---\nMy task', encoding="utf-8"
    )
    await anyio.Path(agent_pkg / "tools").mkdir(parents=True)
    await anyio.Path(agent_pkg / "schedules" / "shared").mkdir(parents=True)
    await anyio.Path(agent_pkg / "schedules" / "shared" / "TASK.md").write_text(
        '---\nname: shared\ncron: "0 12 * * *"\n---\nShared task', encoding="utf-8"
    )

    session_agent = await SessionAgent.create(
        ai_socket="http://x",
        workspace_path=workspace,
        agent_path=agent_pkg,
        session_id="sched-src",
        active_schedules={ACTIVATE_ALL},
    )
    names = {s.name for s in session_agent._schedule_registry.schedules}
    assert names == {"mine"}


@pytest.mark.anyio
async def test_session_without_active_schedules_fires_none(tmp_path: Path) -> None:
    """A user Session reads the entries but fires none - otherwise one reminder is multiplied by live sessions."""
    workspace = tmp_path / "user-ws"
    await anyio.Path(workspace / "schedules" / "mine").mkdir(parents=True)
    await anyio.Path(workspace / "schedules" / "mine" / "TASK.md").write_text(
        '---\nname: mine\ncron: "0 12 * * *"\n---\nMy task', encoding="utf-8"
    )

    session_agent = await SessionAgent.create(
        ai_socket="http://x",
        workspace_path=workspace,
        session_id="plain-user",
    )
    registry = session_agent._schedule_registry
    assert {s.name for s in registry.schedules} == {"mine"}
    assert registry.active_schedules == []


@pytest.mark.anyio
async def test_session_activates_only_named_schedules(tmp_path: Path) -> None:
    """Activation is a property of (session x schedule): named per entry, not one switch per Session."""
    workspace = tmp_path / "user-ws"
    for name in ("mine", "theirs"):
        await anyio.Path(workspace / "schedules" / name).mkdir(parents=True)
        await anyio.Path(workspace / "schedules" / name / "TASK.md").write_text(
            f'---\nname: {name}\ncron: "0 12 * * *"\n---\nT', encoding="utf-8"
        )

    session_agent = await SessionAgent.create(
        ai_socket="http://x",
        workspace_path=workspace,
        session_id="subset-user",
        active_schedules={"mine"},
    )
    registry = session_agent._schedule_registry
    assert {s.name for s in registry.schedules} == {"mine", "theirs"}
    assert {s.name for s in registry.active_schedules} == {"mine"}


@pytest.mark.anyio
async def test_session_without_active_schedules_start_all_starts_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "user-ws"
    await anyio.Path(workspace / "schedules" / "mine").mkdir(parents=True)
    await anyio.Path(workspace / "schedules" / "mine" / "TASK.md").write_text(
        '---\nname: mine\ncron: "* * * * *"\n---\nMy task', encoding="utf-8"
    )
    session_agent = await SessionAgent.create(
        ai_socket="http://x",
        workspace_path=workspace,
        session_id="plain-user-2",
    )
    async with anyio.create_task_group() as tg:
        session_agent.start_all(tg)
        assert session_agent._schedule_registry._runner_scopes == {}
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_runtime_scope_exposes_workspace_and_agent(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")
    ag = str(tmp_path / "ag")
    with runtime_scope(session_id="sid", workspace=ws, agent=ag):
        assert get_workspace() == ws
        assert get_agent() == ag
    assert get_workspace() == ""
    assert get_agent() == ""


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
async def test_agent_abandons_user_message_on_cancel(tmp_path: Path) -> None:
    """SPA Stop / aclose must drop the early-committed user row.

    Otherwise the next send still sees the aborted question in history, and
    the model thinks about both the withdrawn prompt and the edited one.
    """
    history_path = tmp_path / "histories" / "s.jsonl"
    await anyio.Path(history_path.parent).mkdir()

    conv = Conversation(
        messages=[{"role": "system", "content": "original"}],
        path=history_path,
    )
    await conv.save()

    gate = anyio.Event()

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(reasoning="thinking about 述职报告").encode())
        gate.set()
        # Stay open until the client cancels; do not sleep a long wall clock —
        # AppRunner.cleanup waits for in-flight handlers.
        try:
            await request.wait_for_disconnection()
        except Exception:
            await anyio.sleep(2)
        return resp

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(),
            conversation=conv,
        )
        run = agent.run_streamed({"role": "user", "content": "帮我写述职报告"})
        async with aclosing(run):
            async for chunk in run:
                if chunk.reasoning:
                    break
            await gate.wait()

        assert len(agent._conversation.messages) == 1
        assert agent._conversation.messages[0]["role"] == "system"
        loaded = await Conversation._load(history_path)
        assert len(loaded) == 1
        assert loaded[0]["role"] == "system"
    finally:
        await mock_server.cleanup()


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
        expected = MAX_ROUNDS_NOTICE.format(rounds=1)
        assert expected in content
        # The stop must be explicable to the person in the chat, not just to the
        # log: state the cause and carry the actual round count. A bare marker
        # would pass a substring check while still reading as a glitch.
        assert "工具调用上限" in content
        assert "1 轮" in content
        assert "Max tool rounds reached" not in content

        loaded = await Conversation._load(history_path)
        assert any(m.get("content") == expected for m in loaded)
    finally:
        await runner.cleanup()


def test_default_max_tool_rounds_is_single_sourced() -> None:
    """All three entry points must expose the same default.

    They were three separate ``128`` literals, so "change the default" silently
    meant "change one of three". Pinning the number as well as the agreement
    keeps a future edit from lowering one copy and leaving the others.
    """
    assert DEFAULT_MAX_TOOL_ROUNDS == 20
    assert Session.__dataclass_fields__["max_tool_rounds"].default == DEFAULT_MAX_TOOL_ROUNDS
    assert inspect.signature(SessionAgent.__init__).parameters["max_tool_rounds"].default == DEFAULT_MAX_TOOL_ROUNDS
    assert inspect.signature(SessionAgent.create).parameters["max_tool_rounds"].default == DEFAULT_MAX_TOOL_ROUNDS
    assert SessionAgent(ai_client=None)._max_tool_rounds == DEFAULT_MAX_TOOL_ROUNDS  # ty: ignore[invalid-argument-type]


# --- AgentRunResult: terminal mapping (issue #585) ---


async def _run_streamed_against(
    tmp_path: Path,
    sse_body: bytes,
    *,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    tool: ToolFunction | None = None,
) -> AgentRun:
    """Drive one fully-consumed run against a canned SSE body, return the run."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(sse_body)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    server = MockAIServer(tmp_path)
    socket_path = await server.start(handler)
    try:
        registry = (
            ToolRegistry(files={"__test__": FileEntry(file_hash="", tools={tool.name: tool}, funcs={})})
            if tool is not None
            else ToolRegistry()
        )
        agent = SessionAgent(
            ai_client=AiClient(socket_path),
            tool_registry=registry,
            conversation=Conversation(path=tmp_path / "h.jsonl"),
            max_tool_rounds=max_tool_rounds,
        )
        run = agent.run_streamed({"role": "user", "content": "hi"})
        async for _ in run:
            pass
        return run
    finally:
        await server.cleanup()


@pytest.mark.anyio
async def test_run_streamed_result_completed_on_model_stop(tmp_path: Path) -> None:
    run = await _run_streamed_against(tmp_path, _sse_chunk(content="done", finish="stop").encode())

    result = run.result
    assert result is not None
    assert result.status is AgentRunStatus.COMPLETED
    assert result.stop_cause is AgentStopCause.MODEL_COMPLETED
    assert result.model_finish_reason == "stop"
    assert result.model_turns == 1
    assert result.is_complete


@pytest.mark.anyio
async def test_run_streamed_result_incomplete_on_length(tmp_path: Path) -> None:
    """A non-``stop`` model reason keeps its raw value and reads as incomplete."""
    run = await _run_streamed_against(tmp_path, _sse_chunk(content="truncated", finish="length").encode())

    result = run.result
    assert result is not None
    assert result.status is AgentRunStatus.INCOMPLETE
    assert result.stop_cause is AgentStopCause.MODEL_STOPPED
    assert result.model_finish_reason == "length"
    assert not result.is_complete


@pytest.mark.anyio
async def test_run_streamed_result_invalid_stream_without_finish_reason(tmp_path: Path) -> None:
    """No finish reason at all is a broken stream, not a model decision."""
    run = await _run_streamed_against(tmp_path, _sse_chunk(content="dangling").encode())

    result = run.result
    assert result is not None
    assert result.status is AgentRunStatus.INCOMPLETE
    assert result.stop_cause is AgentStopCause.INVALID_MODEL_STREAM
    assert result.model_finish_reason is None


@pytest.mark.anyio
async def test_run_streamed_result_turn_limit(tmp_path: Path) -> None:
    tc = {
        "id": "mock",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c1", "type": "function", "function": {"name": "noop", "arguments": "{}"}}
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    tf = ToolFunction(name="noop", description="X", parameters={"type": "object", "properties": {}, "required": []})
    run = await _run_streamed_against(
        tmp_path,
        f"data: {json.dumps(tc)}\n\n".encode(),
        max_tool_rounds=1,
        tool=tf,
    )

    result = run.result
    assert result is not None
    assert result.status is AgentRunStatus.INCOMPLETE
    assert result.stop_cause is AgentStopCause.AGENT_TURN_LIMIT
    assert result.model_finish_reason == "tool_calls"
    assert result.model_turns == 1


@pytest.mark.anyio
async def test_run_streamed_no_result_on_agent_error(tmp_path: Path) -> None:
    """``AgentRunResult`` and ``AgentError`` are mutually exclusive."""

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(status=500, text="boom")

    server = MockAIServer(tmp_path)
    socket_path = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(socket_path),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=tmp_path / "h.jsonl"),
        )
        run = agent.run_streamed({"role": "user", "content": "hi"})
        with pytest.raises(AgentError):
            async for _ in run:
                pass
        assert run.result is None
    finally:
        await server.cleanup()


@pytest.mark.anyio
async def test_run_streamed_result_is_none_until_stream_exhausted(tmp_path: Path) -> None:
    """Abandoning a run early leaves no result — it never reached a terminal state."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="first").encode())
        await resp.write(_sse_chunk(content="second", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    server = MockAIServer(tmp_path)
    socket_path = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(socket_path),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=tmp_path / "h.jsonl"),
        )
        run = agent.run_streamed({"role": "user", "content": "hi"})
        async with aclosing(run):
            async for _ in run:
                assert run.result is None
                break
        assert run.result is None
    finally:
        await server.cleanup()


@pytest.mark.anyio
async def test_agent_run_result_is_immutable() -> None:
    """Frozen + slotted, asserted by introspection rather than by assignment.

    A literal ``result.status = ...`` is a static type error on a frozen
    dataclass, and routing around it via ``setattr`` would need a ``noqa`` —
    the repo allows neither, so check the guarantee at its source.
    """
    result = AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        stop_cause=AgentStopCause.MODEL_COMPLETED,
        model_finish_reason="stop",
        model_turns=1,
    )
    assert result.__dataclass_params__.frozen
    assert not hasattr(result, "__dict__")  # slots=True

    # Immutability is "derive a new value", not "mutate in place".
    evolved = dataclasses.replace(result, status=AgentRunStatus.INCOMPLETE)
    assert evolved.status is AgentRunStatus.INCOMPLETE
    assert result.status is AgentRunStatus.COMPLETED


@pytest.mark.anyio
async def test_legacy_run_still_yields_chunks_without_result(tmp_path: Path) -> None:
    """``run()`` stays a plain generator so existing callers are untouched."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="hello", finish="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    server = MockAIServer(tmp_path)
    socket_path = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(socket_path),
            tool_registry=ToolRegistry(),
            conversation=Conversation(path=tmp_path / "h.jsonl"),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]
        assert "".join(c.content or "" for c in chunks) == "hello"
    finally:
        await server.cleanup()


# -- tool elapsed time -------------------------------------------------------
#
# Tools run **concurrently** in a task group, so "Executing tool" and "Tool
# result" interleave in the log. Reconstructing one tool's duration by pairing
# those timestamps folds every other tool's waiting into the number, and the
# error gets worse the more tools a turn calls at once. The measurement
# therefore lives in the same function as the measured region.


async def _run_one_tool_turn(
    tmp_path: Path,
    *,
    tool_name: str,
    func,
    arguments: str = "{}",
) -> None:
    """Drive one tool-calling round trip against a mock AI server."""
    request_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal request_count
        request_count += 1
        response = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if request_count == 1:
            call = {
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
                                    "function": {"name": tool_name, "arguments": arguments},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
            await response.write(f"data: {json.dumps(call)}\n\n".encode())
        else:
            await response.write(_sse_chunk(content="done", finish="stop").encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    tool = ToolFunction.from_callable(func)
    server = MockAIServer(tmp_path)
    ai_socket = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(files={"test": FileEntry("", {tool_name: tool}, {tool_name: func})}),
            conversation=Conversation(path=tmp_path / "h.jsonl"),
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "call a tool"})]
    finally:
        await server.cleanup()


@pytest.mark.anyio
async def test_tool_result_log_carries_elapsed_ms(tmp_path: Path) -> None:
    """V1: the duration is a field on the result line, at INFO.

    Production runs INFO, so a DEBUG-only field would be absent exactly when a
    slow tool is the thing being hunted.
    """
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")

    async def slow_tool() -> str:
        """A tool that takes a measurable amount of time."""
        await anyio.sleep(0.05)
        return "ok"

    try:
        await _run_one_tool_turn(tmp_path, tool_name="slow_tool", func=slow_tool)
    finally:
        logger.remove(sink_id)

    results = [m for m in messages if m.startswith("Tool result (")]
    assert len(results) == 1, messages
    match = re.search(r"elapsed_ms=(\d+)", results[0])
    assert match, results[0]
    # Loose lower bound: the tool sleeps 50ms, so a real measurement cannot be 0.
    # Deliberately no upper bound — Windows CI schedulers are erratic and a
    # flaky ceiling would get the assertion deleted rather than fixed.
    assert int(match.group(1)) >= 40, results[0]


@pytest.mark.anyio
async def test_tool_error_log_carries_elapsed_ms(tmp_path: Path) -> None:
    """V2: failures need the duration too.

    "hung for 30s then timed out" and "rejected the arguments instantly" are
    different faults with the same message; only the number separates them.
    """
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="ERROR")

    async def failing_tool() -> str:
        """A tool that raises after a measurable delay."""
        await anyio.sleep(0.05)
        raise RuntimeError("boom")

    try:
        await _run_one_tool_turn(tmp_path, tool_name="failing_tool", func=failing_tool)
    finally:
        logger.remove(sink_id)

    errors = [m for m in messages if m.startswith("Tool execution error (")]
    assert len(errors) == 1, messages
    match = re.search(r"elapsed_ms=(\d+)", errors[0])
    assert match, errors[0]
    assert int(match.group(1)) >= 40, errors[0]


@pytest.mark.anyio
async def test_tool_elapsed_excludes_a_concurrent_tools_waiting(tmp_path: Path) -> None:
    """V3: the number is per tool, not per batch.

    Two tools start together, one sleeping 5x longer. Timestamp pairing would
    charge the fast tool with the slow one's wait; measuring inside the tool's
    own coroutine cannot.
    """
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")

    async def quick() -> str:
        """Returns almost immediately."""
        return "quick"

    async def slow() -> str:
        """Sleeps long enough to dominate the batch."""
        await anyio.sleep(0.25)
        return "slow"

    quick_tool = ToolFunction.from_callable(quick)
    slow_tool = ToolFunction.from_callable(slow)
    request_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal request_count
        request_count += 1
        response = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if request_count == 1:
            call = {
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
                                    "function": {"name": "slow", "arguments": "{}"},
                                },
                                {
                                    "index": 1,
                                    "id": "c2",
                                    "type": "function",
                                    "function": {"name": "quick", "arguments": "{}"},
                                },
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
            await response.write(f"data: {json.dumps(call)}\n\n".encode())
        else:
            await response.write(_sse_chunk(content="done", finish="stop").encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    server = MockAIServer(tmp_path)
    ai_socket = await server.start(handler)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(
                files={
                    "test": FileEntry(
                        "",
                        {"slow": slow_tool, "quick": quick_tool},
                        {"slow": slow, "quick": quick},
                    )
                }
            ),
            conversation=Conversation(path=tmp_path / "h.jsonl"),
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "call two tools"})]
    finally:
        await server.cleanup()
        logger.remove(sink_id)

    # A missing ``elapsed_ms`` is a real regression in the log line, so it must
    # fail as an assertion naming the offending message rather than as an
    # ``AttributeError`` on ``None``.
    elapsed: dict[str, int] = {}
    for m in messages:
        if not m.startswith("Tool result ("):
            continue
        match = re.search(r"elapsed_ms=(\d+)", m)
        assert match is not None, f"no elapsed_ms in {m!r}"
        elapsed[m.split("'")[1]] = int(match.group(1))
    assert set(elapsed) == {"slow", "quick"}, messages
    assert elapsed["slow"] >= 200, elapsed
    # The fast tool must not have inherited the slow one's 250ms wait.
    assert elapsed["quick"] < 100, elapsed
