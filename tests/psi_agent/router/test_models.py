from __future__ import annotations

import math
from typing import Any, cast

import pytest

from psi_agent.router.models import RouterBackendType, RouterMode, RouterTarget
from psi_agent.router.routing import RoutingTarget


def test_routing_target_is_shared_router_target_alias() -> None:
    assert RoutingTarget is RouterTarget
    assert RouterMode("aggregation") is RouterMode.AGGREGATION
    assert RouterMode("fallback") is RouterMode.FALLBACK


def test_router_target_normalizes_surrounding_whitespace() -> None:
    target = RouterTarget(
        candidate_id="  alpha_1  ",
        socket="  http://candidate  ",
        description="  General-purpose candidate  ",
    )

    assert target == RouterTarget(
        candidate_id="alpha_1",
        socket="http://candidate",
        description="General-purpose candidate",
    )


def test_router_target_accepts_an_explicit_router_backend() -> None:
    target = RouterTarget(
        candidate_id="nested",
        socket="nested.sock",
        description="nested router",
        backend_type="router",
    )

    assert target.backend_type == "router"


def test_router_target_normalizes_candidate_timeout() -> None:
    target = RouterTarget(
        candidate_id="candidate-1",
        socket="target.sock",
        description="candidate",
        timeout=3,
    )

    assert target.timeout == 3.0


@pytest.mark.parametrize("timeout", [0, -1, math.inf, -math.inf, math.nan, True, "3"])
def test_router_target_rejects_invalid_candidate_timeout(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        RouterTarget(
            candidate_id="candidate-1",
            socket="target.sock",
            description="candidate",
            timeout=cast(float | None, timeout),
        )


def test_router_target_detaches_request_overrides() -> None:
    overrides: dict[str, Any] = {
        "max_tokens": 512,
        "provider_option": {"nested": ["original"]},
    }

    target = RouterTarget(
        candidate_id="candidate-1",
        socket="target.sock",
        description="candidate",
        request_overrides=overrides,
    )
    overrides["provider_option"]["nested"].append("changed")

    assert target.request_overrides == {
        "max_tokens": 512,
        "provider_option": {"nested": ["original"]},
    }


@pytest.mark.parametrize("field", ["messages", "model", "routing", "stream"])
def test_router_target_rejects_protected_request_overrides(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        RouterTarget(
            candidate_id="candidate-1",
            socket="target.sock",
            description="candidate",
            request_overrides={field: "blocked"},
        )


@pytest.mark.parametrize(
    ("candidate_id", "socket", "description", "message"),
    [
        ("", "socket", "description", "candidate_id"),
        ("_starts_wrong", "socket", "description", "candidate_id"),
        ("contains space", "socket", "description", "candidate_id"),
        ("a" * 65, "socket", "description", "candidate_id"),
        ("valid", " ", "description", "socket"),
        ("valid", "socket", " ", "description"),
    ],
)
def test_router_target_rejects_invalid_public_values(
    candidate_id: str, socket: str, description: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RouterTarget(candidate_id=candidate_id, socket=socket, description=description)


@pytest.mark.parametrize("backend_type", ["session", "", 1, None])
def test_router_target_rejects_unknown_backend_type(backend_type: object) -> None:
    with pytest.raises(ValueError, match="backend_type"):
        RouterTarget(
            candidate_id="valid",
            socket="socket",
            description="description",
            backend_type=cast(RouterBackendType, backend_type),
        )
