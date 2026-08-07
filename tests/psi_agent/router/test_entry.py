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
async def test_router_maps_strict_target_requirement_only_to_aggregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[RouterStrategy] = []

    async def fake_serve(*, session_socket: str, strategy: RouterStrategy) -> None:
        del session_socket
        captured.append(strategy)

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve)
    await Router(
        **_router_kwargs(
            mode="aggregation",
            require_all_targets=True,
        )
    ).run()

    config = cast(Any, captured[0]).config
    assert config.require_all_targets is True

    for mode, router_socket in (("routing", "router-ai.sock"), ("fallback", None)):
        with pytest.raises(ValueError, match="only valid in aggregation"):
            await Router(
                **_router_kwargs(
                    mode=mode,
                    router_socket=router_socket,
                    require_all_targets=True,
                )
            ).run()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "control_field"),
    [
        (RouterMode.ROUTING, "selector_request_overrides"),
        (RouterMode.AGGREGATION, "aggregator_request_overrides"),
        (RouterMode.FALLBACK, None),
    ],
)
async def test_router_maps_control_target_and_candidate_request_overrides(
    mode: RouterMode,
    control_field: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[RouterStrategy] = []

    async def fake_serve(*, session_socket: str, strategy: RouterStrategy) -> None:
        del session_socket
        captured.append(strategy)

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve)
    control: dict[str, Any] = {"max_tokens": 64, "provider_option": {"role": "control"}}
    target: dict[str, Any] = {
        "max_tokens": 1024,
        "provider_option": {"role": "all-targets"},
        "detached": {"values": ["original"]},
    }
    candidates: dict[str, dict[str, Any]] = {
        "candidate-1": {
            "max_tokens": 4096,
            "provider_option": {"role": "candidate-1"},
        }
    }

    await Router(
        **_router_kwargs(
            mode=mode,
            router_socket=None if mode is RouterMode.FALLBACK else "router-ai.sock",
            control_request_overrides={} if mode is RouterMode.FALLBACK else control,
            target_request_overrides=target,
            candidate_request_overrides=candidates,
            candidate_timeouts={"candidate-1": 3.5},
        )
    ).run()

    config = cast(Any, captured[0]).config
    if control_field is not None:
        assert getattr(config, control_field) == control
    assert config.targets[0].request_overrides == {
        "max_tokens": 4096,
        "provider_option": {"role": "candidate-1"},
        "detached": {"values": ["original"]},
    }
    assert config.targets[1].request_overrides == target
    assert config.targets[0].timeout == 3.5
    assert config.targets[1].timeout is None

    control["provider_option"]["role"] = "mutated"
    target["detached"]["values"].append("mutated")
    candidates["candidate-1"]["provider_option"]["role"] = "mutated"
    if control_field is not None:
        assert getattr(config, control_field)["provider_option"] == {"role": "control"}
    assert config.targets[0].request_overrides["provider_option"] == {"role": "candidate-1"}
    assert config.targets[0].request_overrides["detached"] == {"values": ["original"]}
    assert config.targets[1].request_overrides["detached"] == {"values": ["original"]}


@pytest.mark.anyio
@pytest.mark.parametrize("protected", ["messages", "model", "routing", "stream"])
@pytest.mark.parametrize(
    "override_field",
    [
        "control_request_overrides",
        "target_request_overrides",
        "candidate_request_overrides",
    ],
)
async def test_router_rejects_protected_fields_in_every_request_override_scope(
    protected: str,
    override_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)
    value: dict[str, Any]
    if override_field == "candidate_request_overrides":
        value = {"candidate-1": {protected: "blocked"}}
    else:
        value = {protected: "blocked"}

    with pytest.raises(ValueError, match=protected):
        await Router(**_router_kwargs(**{override_field: value})).run()


@pytest.mark.anyio
async def test_fallback_rejects_non_empty_control_request_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)

    with pytest.raises(ValueError, match="does not have a control model"):
        await Router(
            **_router_kwargs(
                mode="fallback",
                router_socket=None,
                control_request_overrides={"max_tokens": 64},
            )
        ).run()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("candidate_timeouts", "match"),
    [
        ([], "candidate_timeouts"),
        ({"": 1}, "candidate_timeouts"),
        ({"candidate-1": 0}, "candidate_timeouts"),
        ({"candidate-1": -1}, "candidate_timeouts"),
        ({"candidate-1": math.inf}, "candidate_timeouts"),
        ({"candidate-1": math.nan}, "candidate_timeouts"),
        ({"candidate-1": True}, "candidate_timeouts"),
        ({"candidate-1": "3"}, "candidate_timeouts"),
        ({"candidate-3": 3}, "unknown candidate"),
    ],
)
async def test_router_rejects_invalid_or_unknown_candidate_timeouts(
    candidate_timeouts: object,
    match: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)

    with pytest.raises(ValueError, match=match):
        await Router(**_router_kwargs(candidate_timeouts=candidate_timeouts)).run()


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
        ("require_all_targets", False),
        ("control_request_overrides", MISSING),
        ("target_request_overrides", MISSING),
        ("candidate_request_overrides", MISSING),
        ("candidate_timeouts", MISSING),
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
