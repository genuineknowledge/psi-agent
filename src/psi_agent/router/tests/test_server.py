from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from psi_agent.router.routing.errors import RouteSelectionError
from psi_agent.router.server import create_router_app


class FakeStrategy:
    def __init__(self, events: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.events = events or []
        self.error = error
        self.started = False
        self.discarded: list[str] = []

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        del body
        self.started = True
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event

    def discard(self, session_id: str) -> None:
        self.discarded.append(session_id)

    def clear(self) -> None:
        self.discarded.clear()


@pytest.mark.anyio
async def test_server_returns_selected_upstream_events_as_sse() -> None:
    event = {"id": "target", "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]}
    server = TestServer(create_router_app(strategy=FakeStrategy([event])))
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.post(
            "/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "routing": {"session_id": "session-a"},
            },
        )
        text = await response.text()
    finally:
        await client.close()

    assert response.status == 200
    payloads = [line.removeprefix("data: ") for line in text.splitlines() if line.startswith("data: ")]
    assert json.loads(payloads[0]) == event
    assert payloads[-1] == "[DONE]"


@pytest.mark.anyio
async def test_server_returns_sse_error_when_strategy_fails_after_prepare() -> None:
    strategy = FakeStrategy(error=RouteSelectionError("bad decision"))
    server = TestServer(create_router_app(strategy=strategy))
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.post(
            "/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "routing": {"session_id": "session-a"},
            },
        )
        text = await response.text()
    finally:
        await client.close()

    payloads = [line.removeprefix("data: ") for line in text.splitlines() if line.startswith("data: ")]
    error_event = json.loads(payloads[0])
    assert response.status == 200
    assert error_event["choices"][0]["finish_reason"] == "error"
    assert "bad decision" in error_event["choices"][0]["delta"]["content"]
    assert strategy.discarded == ["session-a"]


@pytest.mark.anyio
async def test_server_rejects_invalid_request_before_starting_strategy() -> None:
    strategy = FakeStrategy()
    server = TestServer(create_router_app(strategy=strategy))
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.post(
            "/chat/completions",
            json={"messages": "invalid", "stream": True},
        )
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 400
    assert payload["error"]["type"] == "invalid_request_error"
    assert strategy.started is False
