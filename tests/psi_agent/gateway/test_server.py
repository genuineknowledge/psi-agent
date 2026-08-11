from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from psi_agent.gateway.server import _handle_chat, _session_ai_socket

TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


class FakeAIManager:
    def __init__(self, sockets: dict[str, str]) -> None:
        self.sockets = sockets

    def get_socket(self, ai_id: str) -> str:
        return self.sockets[ai_id]


@dataclass(frozen=True)
class FakeSession:
    id: str
    backend_type: str
    backend_id: str


class FakeSessionManager:
    async def list_all(self) -> list[FakeSession]:
        return [FakeSession("session-1", "router", "router-1")]


@dataclass(frozen=True)
class FakeRouter:
    mode: str
    router_ai_id: str | None


class FakeRouterManager:
    def __init__(self, mode: str = "aggregation") -> None:
        self.mode = mode

    def get(self, router_id: str) -> FakeRouter:
        assert router_id == "router-1"
        return FakeRouter(
            mode=self.mode,
            router_ai_id=None if self.mode == "fallback" else "aggregator",
        )

    def get_socket(self, router_id: str) -> str:
        assert router_id == "router-1"
        return "fallback-public.sock"


@pytest.mark.anyio
async def test_title_socket_for_router_backend_uses_router_ai_id() -> None:
    app = web.Application()
    app["aim"] = FakeAIManager({"aggregator": "aggregate.sock", "upstream": "upstream.sock"})
    app["sm"] = FakeSessionManager()
    app["rm"] = FakeRouterManager()
    request = make_mocked_request("POST", "/titles/generate", app=app)

    assert await _session_ai_socket(request, "session-1") == "aggregate.sock"


@pytest.mark.anyio
async def test_title_socket_for_fallback_backend_uses_public_router_socket() -> None:
    app = web.Application()
    app["aim"] = FakeAIManager({"aggregator": "aggregate.sock"})
    app["sm"] = FakeSessionManager()
    app["rm"] = FakeRouterManager(mode="fallback")
    request = make_mocked_request("POST", "/titles/generate", app=app)

    assert await _session_ai_socket(request, "session-1") == "fallback-public.sock"


@pytest.mark.anyio
async def test_chat_stream_marks_unhandled_failure_fatal() -> None:
    class FakeSessionManagerForChat:
        @staticmethod
        def get_socket(session_id: str) -> str:
            assert session_id == "session-1"
            return "unused-channel-socket"

    class FailingChatManager:
        async def handle(
            self,
            channel_socket: str,
            body: dict[str, Any],
            *,
            trace_id: str | None = None,
        ) -> AsyncGenerator[dict[str, Any]]:
            assert channel_socket == "unused-channel-socket"
            assert body == {"chunks": []}
            assert trace_id == TRACE_ID
            if False:
                yield {}
            raise RuntimeError("upstream failed")

    app = web.Application()
    app["sm"] = FakeSessionManagerForChat()
    app["cm"] = FailingChatManager()
    app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/sessions/session-1/chat",
            json={"chunks": []},
            headers={"X-Psi-Trace-Id": TRACE_ID},
        )
        body = await response.text()
    finally:
        await client.close()

    assert response.status == 200
    assert '"type": "error"' in body
    assert '"severity": "fatal"' in body
    assert '"code": "chat_failed"' in body
    assert f'"trace_id": "{TRACE_ID}"' in body
    assert response.headers["X-Psi-Trace-Id"] == TRACE_ID
    assert body.endswith("data: [DONE]\n\n")


@pytest.mark.anyio
async def test_chat_rejects_invalid_trace_id_before_starting_stream() -> None:
    class SessionManager:
        @staticmethod
        def get_socket(session_id: str) -> str:
            raise AssertionError("invalid trace must be rejected before Session lookup")

    app = web.Application()
    app["sm"] = SessionManager()
    app["cm"] = object()
    app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/sessions/session-1/chat",
            json={"chunks": []},
            headers={"X-Psi-Trace-Id": "not-a-uuid"},
        )
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 400
    assert "Invalid trace ID" in body["error"]
