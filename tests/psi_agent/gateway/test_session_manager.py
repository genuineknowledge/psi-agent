from __future__ import annotations

from typing import cast

import pytest

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._router_manager import RouterManager
from psi_agent.gateway._session_manager import SessionManager


class BackendManager:
    def __init__(self, sockets: dict[str, str]) -> None:
        self.sockets = sockets

    def get_socket(self, backend_id: str) -> str:
        if backend_id not in self.sockets:
            raise LookupError(backend_id)
        return self.sockets[backend_id]


def test_resolve_ai_and_router_backend_sockets() -> None:
    manager = SessionManager(
        _aim=cast(AIManager, BackendManager({"ai-1": "http://ai"})),
        _rm=cast(RouterManager, BackendManager({"router-1": "http://router"})),
        _prefix="gw",
        _tg=None,
    )
    assert manager.resolve_backend_socket("ai", "ai-1") == "http://ai"
    assert manager.resolve_backend_socket("router", "router-1") == "http://router"
    with pytest.raises(ValueError, match="backend_type"):
        manager.resolve_backend_socket("other", "x")
