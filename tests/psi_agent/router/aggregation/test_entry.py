"""Standalone aggregation Router entry contracts."""

from __future__ import annotations

import pytest

from psi_agent.router.aggregation import AggregationRouter, AggregationStrategy
from psi_agent.router.models import RouterTarget


@pytest.mark.anyio
async def test_aggregation_router_run_sets_up_logging_before_config_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured: list[bool] = []
    monkeypatch.setattr(
        "psi_agent.router.aggregation.entry.setup_logging",
        lambda *, verbose: configured.append(verbose),
    )

    with pytest.raises(ValueError, match="session_socket"):
        await AggregationRouter(
            session_socket="",
            aggregator_socket="aggregate.sock",
            targets=[],
        ).run()

    assert configured == [False]


@pytest.mark.anyio
async def test_aggregation_router_builds_one_client_and_serves_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured: list[bool] = []
    served: list[tuple[str, object]] = []
    created_clients: list[object] = []

    class FakeClient:
        def __init__(self) -> None:
            created_clients.append(self)

    async def fake_serve_router(*, session_socket: str, strategy: object) -> None:
        served.append((session_socket, strategy))

    monkeypatch.setattr(
        "psi_agent.router.aggregation.entry.setup_logging",
        lambda *, verbose: configured.append(verbose),
    )
    monkeypatch.setattr("psi_agent.router.aggregation.entry.RouterHttpClient", FakeClient)
    monkeypatch.setattr("psi_agent.router.aggregation.entry.serve_router", fake_serve_router)

    await AggregationRouter(
        session_socket="router.sock",
        aggregator_socket="aggregate.sock",
        targets=[RouterTarget("candidate-1", "target.sock", "general")],
        verbose=True,
    ).run()

    assert configured == [True]
    assert len(created_clients) == 1
    assert len(served) == 1
    session_socket, strategy = served[0]
    assert session_socket == "router.sock"
    assert isinstance(strategy, AggregationStrategy)
    assert strategy.client is created_clients[0]
