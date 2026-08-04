from __future__ import annotations

from dataclasses import dataclass

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from psi_agent.gateway.server import _session_ai_socket


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
    router_ai_id: str


class FakeRouterManager:
    def get(self, router_id: str) -> FakeRouter:
        assert router_id == "router-1"
        return FakeRouter(router_ai_id="aggregator")


@pytest.mark.anyio
async def test_title_socket_for_router_backend_uses_router_ai_id() -> None:
    app = web.Application()
    app["aim"] = FakeAIManager({"aggregator": "aggregate.sock", "upstream": "upstream.sock"})
    app["sm"] = FakeSessionManager()
    app["rm"] = FakeRouterManager()
    request = make_mocked_request("POST", "/titles/generate", app=app)

    assert await _session_ai_socket(request, "session-1") == "aggregate.sock"
