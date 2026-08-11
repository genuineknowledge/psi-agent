from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import anyio
import pytest
from aiohttp import ClientSession, web
from anyio.lowlevel import checkpoint

from psi_agent._router_status import RouterStatus
from psi_agent.router.errors import RouterError
from psi_agent.router.server import create_router_app, serve_router

TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


@dataclass
class FakeStrategy:
    events: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    release_after_first: anyio.Event | None = None
    closed: anyio.Event = field(default_factory=anyio.Event)
    received: list[dict[str, Any]] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    clear_calls: int = 0

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        self.received.append(body)
        try:
            for index, event in enumerate(self.events):
                yield event
                if index == 0 and self.release_after_first is not None:
                    await self.release_after_first.wait()
            if self.error is not None:
                raise self.error
        finally:
            self.closed.set()

    def discard(self, session_id: str) -> None:
        self.discarded.append(session_id)

    def clear(self) -> None:
        self.clear_calls += 1


@asynccontextmanager
async def _serve(strategy: FakeStrategy) -> AsyncIterator[str]:
    runner = web.AppRunner(create_router_app(strategy=strategy), handler_cancellation=True)
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
        "stream": True,
        "routing": {"session_id": "session-a", "trace_id": TRACE_ID},
    }


async def _post(
    url: str,
    *,
    payload: object | None = None,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, str]]:
    async with ClientSession() as session:
        if raw is None:
            response = await session.post(f"{url}/chat/completions", json=payload, headers=headers)
        else:
            response = await session.post(f"{url}/chat/completions", data=raw, headers=headers)
        return response.status, await response.text(), dict(response.headers)


def _sse_payloads(text: str) -> list[object]:
    return [
        "[DONE]" if line == "data: [DONE]" else json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "raw"),
    [
        (None, b"{"),
        (None, b"[]"),
        ({"messages": "bad"}, None),
        ({"messages": ["bad"]}, None),
        ({"messages": [], "tools": "bad"}, None),
        ({"messages": [], "tools": ["bad"]}, None),
        ({"messages": [], "stream": False}, None),
        ({"messages": [], "routing": "bad"}, None),
        ({"messages": [], "routing": {"session_id": " "}}, None),
        ({"messages": [], "routing": {"session_id": 7}}, None),
        ({"messages": [], "routing": {"trace_id": "not-a-uuid"}}, None),
    ],
)
async def test_invalid_request_returns_openai_http_error_before_prepare(
    payload: object | None,
    raw: bytes | None,
) -> None:
    strategy = FakeStrategy()
    async with _serve(strategy) as url:
        status, text, headers = await _post(url, payload=payload, raw=raw)

    error = json.loads(text)["error"]
    assert status == 400
    assert headers["Content-Type"].startswith("application/json")
    assert error["type"] == "invalid_request_error"
    assert error["param"] is None
    assert error["code"] == 400
    assert strategy.received == []


@pytest.mark.anyio
async def test_valid_strategy_events_are_single_choice_sse_followed_by_done() -> None:
    events = [
        RouterStatus(
            trace_id=TRACE_ID,
            mode="routing",
            phase="selecting",
        ).to_event(),
        {"choices": [{"index": 0, "delta": {"content": "final"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    strategy = FakeStrategy(events=events)

    async with _serve(strategy) as url:
        status, text, headers = await _post(url, payload=_body())

    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    assert headers["X-Psi-Trace-Id"] == TRACE_ID
    assert _sse_payloads(text) == [*events, "[DONE]"]
    assert all(len(cast(dict[str, Any], event)["choices"]) == 1 for event in _sse_payloads(text)[:-1])


@pytest.mark.anyio
async def test_trace_header_must_match_router_metadata() -> None:
    strategy = FakeStrategy()
    async with _serve(strategy) as url:
        status, text, _ = await _post(
            url,
            payload=_body(),
            headers={"X-Psi-Trace-Id": "123e4567-e89b-12d3-a456-426614174001"},
        )

    assert status == 400
    assert "must match" in text
    assert strategy.received == []


@pytest.mark.anyio
async def test_strategy_exception_after_prepare_emits_one_router_error_frame() -> None:
    strategy = FakeStrategy(error=RouterError("strategy failed"))

    async with _serve(strategy) as url:
        status, text, _ = await _post(url, payload=_body())

    payloads = _sse_payloads(text)
    assert status == 200
    assert len(payloads) == 1
    assert cast(dict[str, Any], payloads[0])["choices"] == [
        {
            "index": 0,
            "delta": {"content": "[Router Error]: strategy failed"},
            "finish_reason": "error",
        }
    ]
    assert strategy.discarded == ["session-a"]


@pytest.mark.anyio
async def test_non_single_choice_strategy_event_becomes_one_router_error_frame() -> None:
    strategy = FakeStrategy(
        events=[
            {
                "choices": [
                    {"index": 0, "delta": {"content": "one"}, "finish_reason": None},
                    {"index": 1, "delta": {"content": "two"}, "finish_reason": None},
                ]
            }
        ]
    )

    async with _serve(strategy) as url:
        _, text, _ = await _post(url, payload=_body())

    payloads = _sse_payloads(text)
    assert len(payloads) == 1
    assert cast(dict[str, Any], payloads[0])["choices"][0]["finish_reason"] == "error"


@pytest.mark.anyio
async def test_client_disconnect_closes_strategy_generator() -> None:
    release = anyio.Event()
    strategy = FakeStrategy(
        events=[
            {"choices": [{"index": 0, "delta": {"content": "first"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": "second"}, "finish_reason": "stop"}]},
        ],
        release_after_first=release,
    )

    async with _serve(strategy) as url, ClientSession() as session:
        response = await session.post(f"{url}/chat/completions", json=_body())
        assert await response.content.readline() == (
            b'data: {"choices": [{"index": 0, "delta": {"content": "first"}, "finish_reason": null}]}\n'
        )
        response.close()
        release.set()
        with anyio.fail_after(2):
            await strategy.closed.wait()


@pytest.mark.anyio
async def test_startup_failure_clears_strategy_and_completes_shielded_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = FakeStrategy()
    cleaned = anyio.Event()
    outer_scope = anyio.CancelScope()

    class FailingRunner:
        async def setup(self) -> None:
            outer_scope.cancel()
            raise RuntimeError("setup failed")

        async def cleanup(self) -> None:
            await checkpoint()
            cleaned.set()

    monkeypatch.setattr("psi_agent.router.server.web.AppRunner", lambda *args, **kwargs: FailingRunner())

    with pytest.raises(RuntimeError, match="setup failed"), outer_scope:
        await serve_router(session_socket="router.sock", strategy=strategy)

    assert strategy.clear_calls == 1
    assert cleaned.is_set()


@pytest.mark.anyio
async def test_startup_cancellation_clears_strategy_and_completes_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = FakeStrategy()
    setup_started = anyio.Event()
    cleaned = anyio.Event()

    class PendingRunner:
        async def setup(self) -> None:
            setup_started.set()
            await anyio.sleep_forever()

        async def cleanup(self) -> None:
            await checkpoint()
            cleaned.set()

    monkeypatch.setattr("psi_agent.router.server.web.AppRunner", lambda *args, **kwargs: PendingRunner())

    async def run_server() -> None:
        await serve_router(session_socket="router.sock", strategy=strategy)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_server)
        await setup_started.wait()
        task_group.cancel_scope.cancel()

    assert strategy.clear_calls == 1
    assert cleaned.is_set()


@pytest.mark.anyio
async def test_cancellation_clears_strategy_and_completes_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = FakeStrategy()
    started = anyio.Event()
    cleaned = anyio.Event()

    class FakeRunner:
        async def setup(self) -> None:
            return None

        async def cleanup(self) -> None:
            await checkpoint()
            cleaned.set()

    class FakeSite:
        async def start(self) -> None:
            started.set()

    monkeypatch.setattr("psi_agent.router.server.web.AppRunner", lambda *args, **kwargs: FakeRunner())
    monkeypatch.setattr("psi_agent.router.server.create_site", lambda runner, socket: FakeSite())

    async def run_server() -> None:
        await serve_router(session_socket="router.sock", strategy=strategy)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_server)
        await started.wait()
        task_group.cancel_scope.cancel()

    assert strategy.clear_calls == 1
    assert cleaned.is_set()
