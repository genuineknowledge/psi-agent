from __future__ import annotations

import json
import socket
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from aiohttp import ClientSession, web

from psi_agent.router.aggregation.orchestrator import OrchestrationError
from psi_agent.router.client import RouterUpstreamError, UpstreamResult
from psi_agent.router.protocol import RouterConfig
from psi_agent.router.server import (
    _ROUTER_CLIENT_KEY,
    _ROUTER_CONFIG_KEY,
    _ROUTER_STRATEGY_KEY,
    handle_chat_completions,
    serve_router,
)


@dataclass
class FakeOrchestrator:
    outcome: UpstreamResult | Exception
    received: list[dict[str, Any]] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    async def process(self, *, body: dict[str, Any]) -> UpstreamResult:
        self.received.append(body)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def discard(self, session_id: str) -> None:
        self.discarded.append(session_id)

    def clear(self) -> None:
        return None


@dataclass
class FakeClient:
    chunks: list[bytes] = field(default_factory=list)
    error: Exception | None = None
    calls: list[tuple[str, dict[str, Any], float | None]] = field(default_factory=list)
    fail_after_first_chunk: bool = False

    async def stream_raw(self, *, socket: str, body: dict[str, Any], **options: Any) -> AsyncGenerator[bytes]:
        timeout = options.get("timeout")
        assert timeout is None or isinstance(timeout, (float, int))
        self.calls.append((socket, body, timeout))
        if self.error is not None and not self.fail_after_first_chunk:
            raise self.error
        for index, chunk in enumerate(self.chunks):
            yield chunk
            if index == 0 and self.error is not None:
                raise self.error


async def _serve(*, strategy: FakeOrchestrator, client: FakeClient) -> AsyncIterator[str]:
    app = web.Application()
    app[_ROUTER_CONFIG_KEY] = RouterConfig(
        session_socket="router-listener",
        router_socket="router-ai",
        default_socket="default-ai",
        mode="aggregation",
        upstream=[("branch-ai", "general")],
        router_timeout=12.0,
    )
    app[_ROUTER_STRATEGY_KEY] = strategy
    app[_ROUTER_CLIENT_KEY] = client
    app.router.add_post("/chat/completions", handle_chat_completions)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = cast(Any, site._server).sockets if site._server is not None else []
    assert sockets
    port = sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


def _body() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.3,
        "unknown_parameter": {"preserved": True},
        "routing": {"session_id": "session-a"},
        "model": "internal-routing-model",
    }


def _unused_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


async def _post(
    url: str, *, payload: object | None = None, raw: bytes | None = None
) -> tuple[int, str, dict[str, str]]:
    async with ClientSession() as session:
        if raw is None:
            response = await session.post(f"{url}/chat/completions", json=payload)
        else:
            response = await session.post(f"{url}/chat/completions", data=raw)
        return response.status, await response.text(), dict(response.headers)


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [b"{", b"[]"])
async def test_handler_returns_http_400_before_streaming_for_invalid_or_non_object_json(raw: bytes) -> None:
    orchestrator = FakeOrchestrator(UpstreamResult(content="unused", finish_reason="stop"))
    async for url in _serve(strategy=orchestrator, client=FakeClient()):
        status, text, headers = await _post(url, raw=raw)

    assert status == 400
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(text)["error"]["type"] == "invalid_request_error"
    assert orchestrator.received == []


@pytest.mark.anyio
async def test_handler_encodes_a_successful_result_as_one_choice_sse_chunk() -> None:
    orchestrator = FakeOrchestrator(UpstreamResult(content="final answer", finish_reason="stop"))
    async for url in _serve(strategy=orchestrator, client=FakeClient()):
        status, text, headers = await _post(url, payload=_body())

    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    payload = json.loads(text.removeprefix("data: ").strip())
    assert payload["choices"] == [{"index": 0, "delta": {"content": "final answer"}, "finish_reason": "stop"}]
    assert orchestrator.received == [_body()]


@pytest.mark.anyio
async def test_handler_encodes_branch_tool_calls_as_one_choice_sse_chunk() -> None:
    tool_calls = [{"id": "call-1", "type": "function", "function": {"name": "search", "arguments": "{}"}}]
    orchestrator = FakeOrchestrator(
        UpstreamResult(content="Processing subtask 1: search", tool_calls=tool_calls, finish_reason="tool_calls")
    )
    async for url in _serve(strategy=orchestrator, client=FakeClient()):
        status, text, _ = await _post(url, payload=_body())

    assert status == 200
    payload = json.loads(text.removeprefix("data: ").strip())
    assert len(payload["choices"]) == 1
    assert payload["choices"][0]["delta"] == {"content": "Processing subtask 1: search", "tool_calls": tool_calls}
    assert payload["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        OrchestrationError("planner failure"),
        RouterUpstreamError("branch failure"),
        OrchestrationError("aggregation failure"),
    ],
)
async def test_handler_falls_back_once_and_preserves_only_public_request_fields(error: Exception) -> None:
    orchestrator = FakeOrchestrator(error)
    client = FakeClient(chunks=[b"data: default answer\n\n"])
    async for url in _serve(strategy=orchestrator, client=client):
        status, text, _ = await _post(url, payload=_body())

    assert status == 200
    assert text == "data: default answer\n\n"
    assert orchestrator.discarded == ["session-a"]
    assert client.calls == [
        (
            "default-ai",
            {
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [{"type": "function", "function": {"name": "search"}}],
                "temperature": 0.3,
                "unknown_parameter": {"preserved": True},
            },
            12.0,
        )
    ]


@pytest.mark.anyio
async def test_handler_returns_http_502_if_default_fallback_fails_before_response_prepare() -> None:
    orchestrator = FakeOrchestrator(OrchestrationError("planner failure"))
    client = FakeClient(error=RouterUpstreamError("default unavailable"))
    async for url in _serve(strategy=orchestrator, client=client):
        status, text, headers = await _post(url, payload=_body())

    assert status == 502
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(text)["error"]["code"] == 502
    assert len(client.calls) == 1


@pytest.mark.anyio
async def test_handler_emits_one_choice_sse_error_if_default_stream_fails_after_prepare() -> None:
    orchestrator = FakeOrchestrator(OrchestrationError("branch failure"))
    client = FakeClient(
        chunks=[b"data: partial default\n\n"],
        error=RouterUpstreamError("default stream interrupted"),
        fail_after_first_chunk=True,
    )
    async for url in _serve(strategy=orchestrator, client=client):
        status, text, _ = await _post(url, payload=_body())

    assert status == 200
    lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert lines[0] == "data: partial default"
    error_chunk = json.loads(lines[1].removeprefix("data: "))
    assert error_chunk["choices"] == [
        {
            "index": 0,
            "delta": {"content": "[Router Error]: default stream interrupted"},
            "finish_reason": "error",
        }
    ]
    assert len(client.calls) == 1


@pytest.mark.anyio
async def test_serve_router_accepts_strategy_protocol_and_streams_result(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = FakeOrchestrator(UpstreamResult(content="strategy answer", finish_reason="stop"))

    async def fake_sleep_forever() -> None:
        return None

    monkeypatch.setattr("psi_agent.router.server.anyio.sleep_forever", fake_sleep_forever)
    config = RouterConfig(
        session_socket=f"http://127.0.0.1:{_unused_tcp_port()}",
        router_socket="router-ai",
        default_socket="default-ai",
        mode="aggregation",
        upstream=[("branch-ai", "general")],
    )
    await serve_router(config=config, strategy=strategy, client=FakeClient())

    assert strategy.received == []
