"""Standalone routing Router entry contracts."""

from __future__ import annotations

from typing import cast

import pytest

from psi_agent.router.models import RouterTarget
from psi_agent.router.routing import RouteSelector, RoutingRouter, RoutingStrategy


@pytest.mark.anyio
async def test_routing_router_sets_up_logging_before_config_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    configured: list[bool] = []
    monkeypatch.setattr(
        "psi_agent.router.routing.entry.setup_logging",
        lambda *, verbose: configured.append(verbose),
    )

    with pytest.raises(ValueError, match="session_socket"):
        await RoutingRouter(session_socket="", selector_socket="selector.sock", targets=[]).run()

    assert configured == [False]


@pytest.mark.anyio
async def test_routing_router_builds_one_shared_client_and_serves_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    configured: list[bool] = []
    served: list[tuple[str, object]] = []
    clients: list[object] = []

    class FakeClient:
        def __init__(self) -> None:
            clients.append(self)

    async def fake_serve_router(*, session_socket: str, strategy: object) -> None:
        served.append((session_socket, strategy))

    monkeypatch.setattr(
        "psi_agent.router.routing.entry.setup_logging",
        lambda *, verbose: configured.append(verbose),
    )
    monkeypatch.setattr("psi_agent.router.routing.entry.RouterHttpClient", FakeClient)
    monkeypatch.setattr("psi_agent.router.routing.entry.serve_router", fake_serve_router)

    await RoutingRouter(
        session_socket="router.sock",
        selector_socket="selector.sock",
        targets=[RouterTarget("candidate-1", "target.sock", "general")],
        verbose=True,
    ).run()

    assert configured == [True]
    assert len(clients) == 1
    assert len(served) == 1
    session_socket, strategy = served[0]
    assert session_socket == "router.sock"
    assert isinstance(strategy, RoutingStrategy)
    assert strategy.client is clients[0]
    assert cast(RouteSelector, strategy.selector).client is clients[0]
