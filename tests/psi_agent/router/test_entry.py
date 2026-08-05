from __future__ import annotations

import math
from dataclasses import MISSING, fields
from typing import Any, cast

import pytest

import psi_agent.router as router_package
from psi_agent.router import (
    AggregationConfig,
    AggregationStrategy,
    FallbackConfig,
    FallbackStrategy,
    Router,
    RouterHttpClient,
    RouterMode,
    RouterStrategy,
    RoutingConfig,
    RoutingStrategy,
)


def _router_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "session_socket": "router.sock",
        "router_socket": "router-ai.sock",
        "mode": "routing",
        "upstream": [("one.sock", "one"), ("two.sock", "two")],
    }
    values.update(overrides)
    return values


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["routing", RouterMode.AGGREGATION, RouterMode.FALLBACK])
async def test_router_assigns_stable_candidate_ids_and_builds_selected_strategy(
    mode: RouterMode | str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[RouterStrategy] = []

    async def fake_serve(*, session_socket: str, strategy: RouterStrategy) -> None:
        assert session_socket == "router.sock"
        captured.append(strategy)

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve)
    await Router(
        session_socket="router.sock",
        router_socket=None if mode is RouterMode.FALLBACK else "router-ai.sock",
        mode=mode,
        upstream=[("one.sock", "one"), ("two.sock", "two")],
        target_timeout=5,
    ).run()

    strategy = cast(Any, captured[0])
    assert [target.candidate_id for target in strategy.config.targets] == [
        "candidate-1",
        "candidate-2",
    ]
    if mode == "routing":
        assert isinstance(captured[0], RoutingStrategy)
    elif mode is RouterMode.AGGREGATION:
        assert isinstance(captured[0], AggregationStrategy)
    else:
        assert isinstance(captured[0], FallbackStrategy)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "config_type", "timeout_field", "context_field"),
    [
        (RouterMode.ROUTING, RoutingConfig, "selector_timeout", "max_selection_chars"),
        (RouterMode.AGGREGATION, AggregationConfig, "aggregator_timeout", "max_context_chars"),
    ],
)
async def test_router_maps_shared_limits_to_selected_mode_config(
    mode: RouterMode,
    config_type: type[RoutingConfig] | type[AggregationConfig],
    timeout_field: str,
    context_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[RouterStrategy] = []

    async def fake_serve(*, session_socket: str, strategy: RouterStrategy) -> None:
        del session_socket
        captured.append(strategy)

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve)
    await Router(
        **_router_kwargs(
            mode=mode,
            router_timeout=7.5,
            target_timeout=2.5,
            max_context_chars=9876,
        )
    ).run()

    config = cast(Any, captured[0]).config
    assert isinstance(config, config_type)
    assert getattr(config, timeout_field) == 7.5
    assert config.target_timeout == 2.5
    assert getattr(config, context_field) == 9876


@pytest.mark.anyio
async def test_router_constructs_one_http_client_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[object] = []
    strategies: list[RouterStrategy] = []

    class FakeClient(RouterHttpClient):
        def __init__(self) -> None:
            clients.append(self)

    async def fake_serve(*, session_socket: str, strategy: RouterStrategy) -> None:
        del session_socket
        strategies.append(strategy)

    monkeypatch.setattr("psi_agent.router.entry.RouterHttpClient", FakeClient)
    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve)
    await Router(**_router_kwargs(mode="aggregation")).run()

    assert len(clients) == 1
    assert cast(Any, strategies[0]).client is clients[0]


@pytest.mark.anyio
async def test_router_configures_logging_before_invalid_mode_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_setup_logging(*, verbose: bool) -> None:
        calls.append(("logging", verbose))

    monkeypatch.setattr("psi_agent.router.entry.setup_logging", fake_setup_logging)
    with pytest.raises(ValueError, match="mode"):
        await Router(**_router_kwargs(mode="unknown", verbose=True)).run()

    assert calls == [("logging", True)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "upstream",
    [
        (("one.sock", "one"),),
        [],
        [("one.sock",)],
        [("one.sock", "one", "extra")],
        [["one.sock", "one"]],
        [(1, "one")],
        [("one.sock", 1)],
    ],
)
async def test_router_rejects_non_list_or_malformed_upstream_pairs(
    upstream: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)

    with pytest.raises(ValueError, match="upstream"):
        await Router(**_router_kwargs(upstream=upstream)).run()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"upstream": [("one.sock", "one"), ("one.sock", "duplicate")]},
        {"session_socket": "one.sock"},
        {"mode": "aggregation", "router_socket": "one.sock"},
        {"router_timeout": False},
        {"router_timeout": math.nan},
        {"target_timeout": math.inf},
        {"max_context_chars": False},
        {"max_context_chars": 0},
    ],
)
async def test_router_selected_config_owns_collision_and_limit_validation(
    overrides: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)

    with pytest.raises(ValueError):
        await Router(**_router_kwargs(**overrides)).run()


def test_router_facade_has_only_current_required_and_optional_fields() -> None:
    assert [(field.name, field.default) for field in fields(Router)] == [
        ("session_socket", MISSING),
        ("router_socket", MISSING),
        ("mode", MISSING),
        ("upstream", MISSING),
        ("upstream_types", MISSING),
        ("router_timeout", 30.0),
        ("target_timeout", None),
        ("max_context_chars", 12_000),
        ("verbose", False),
    ]
    with pytest.raises(TypeError):
        cast(Any, Router)(
            session_socket="router.sock",
            router_socket="router-ai.sock",
            upstream=[("one.sock", "one")],
        )


def test_router_root_exports_current_modes_without_removed_process_apis() -> None:
    expected = {
        "AggregationConfig",
        "AggregationError",
        "AggregationFeedback",
        "AggregationRouter",
        "AggregationStrategy",
        "FallbackConfig",
        "FallbackError",
        "FallbackRouter",
        "FallbackStrategy",
        "Router",
        "RouterMode",
        "RouterTarget",
        "RoutingConfig",
        "RoutingRouter",
        "RoutingStrategy",
        "compact_feedback",
        "build_aggregation_messages",
    }
    assert expected <= set(router_package.__all__)
    assert not {
        "Planner",
        "RouterClient",
        "UpstreamResult",
        "stream_raw",
        "Orchestrator",
    } & set(router_package.__all__)


@pytest.mark.anyio
async def test_router_builds_fallback_with_typed_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[RouterStrategy] = []

    async def fake_serve(*, session_socket: str, strategy: RouterStrategy) -> None:
        assert session_socket == "router.sock"
        captured.append(strategy)

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve)
    await Router(
        session_socket="router.sock",
        router_socket=None,
        mode="fallback",
        upstream=[("one.sock", "one"), ("nested.sock", "nested")],
        upstream_types=["ai", "router"],
        target_timeout=5,
    ).run()

    strategy = cast(FallbackStrategy, captured[0])
    assert isinstance(strategy.config, FallbackConfig)
    assert [target.backend_type for target in strategy.config.targets] == ["ai", "router"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "fallback", "router_socket": "control.sock"},
        {"mode": "routing", "router_socket": None},
        {"upstream": [("one.sock", "one", "router")], "upstream_types": ["router"]},
        {"upstream_types": ["ai"]},
        {"upstream_types": ["unknown", "ai"]},
    ],
)
async def test_router_rejects_invalid_mode_specific_or_typed_configuration(
    overrides: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)
    with pytest.raises(ValueError):
        await Router(**_router_kwargs(**overrides)).run()
