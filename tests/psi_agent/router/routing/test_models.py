"""Contracts for routing configuration."""

from __future__ import annotations

import math
from typing import cast

import pytest

from psi_agent.router.models import RouterTarget
from psi_agent.router.routing import RoutingConfig


def _target(candidate_id: str = "candidate-1", socket: str = "target-1.sock") -> RouterTarget:
    return RouterTarget(candidate_id, socket, "general")


def test_routing_config_normalizes_fields_and_targets() -> None:
    config = RoutingConfig(
        session_socket=" router.sock ",
        selector_socket=" selector.sock ",
        targets=[_target()],
    )

    assert config.session_socket == "router.sock"
    assert config.selector_socket == "selector.sock"
    assert config.targets == (_target(),)


def test_routing_config_allows_selector_to_be_a_candidate() -> None:
    target = _target(socket="selector.sock")

    config = RoutingConfig(
        session_socket="router.sock",
        selector_socket="selector.sock",
        targets=[target],
    )

    assert config.targets == (target,)


@pytest.mark.parametrize(
    ("session_socket", "selector_socket", "targets", "match"),
    [
        ("", "selector.sock", [_target()], "session_socket"),
        ("router.sock", "", [_target()], "selector_socket"),
        ("router.sock", "router.sock", [_target()], "selector_socket"),
        ("router.sock", "selector.sock", [], "targets"),
        ("router.sock", "selector.sock", [object()], "RoutingTarget"),
        ("router.sock", "selector.sock", [_target(socket="router.sock")], "session_socket"),
        ("router.sock", "selector.sock", [_target(), _target()], "candidate_id"),
        (
            "router.sock",
            "selector.sock",
            [_target(), _target(candidate_id="candidate-2")],
            "socket",
        ),
    ],
)
def test_routing_config_rejects_invalid_or_colliding_targets(
    session_socket: str,
    selector_socket: str,
    targets: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        RoutingConfig(
            session_socket=session_socket,
            selector_socket=selector_socket,
            targets=cast(list[RouterTarget], targets),
        )


@pytest.mark.parametrize("timeout", [0, -1, math.inf, -math.inf, math.nan, True, "30"])
@pytest.mark.parametrize("field", ["selector_timeout", "target_timeout"])
def test_routing_config_rejects_invalid_timeouts(field: str, timeout: object) -> None:
    with pytest.raises(ValueError, match=field):
        if field == "selector_timeout":
            RoutingConfig(
                session_socket="router.sock",
                selector_socket="selector.sock",
                targets=[_target()],
                selector_timeout=cast(float | None, timeout),
            )
        else:
            RoutingConfig(
                session_socket="router.sock",
                selector_socket="selector.sock",
                targets=[_target()],
                target_timeout=cast(float | None, timeout),
            )


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "12000"])
def test_routing_config_requires_positive_integer_selection_budget(budget: object) -> None:
    with pytest.raises(ValueError, match="max_selection_chars"):
        RoutingConfig(
            session_socket="router.sock",
            selector_socket="selector.sock",
            targets=[_target()],
            max_selection_chars=cast(int, budget),
        )
