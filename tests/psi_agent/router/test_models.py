from __future__ import annotations

import pytest

from psi_agent.router.models import RouterMode, RouterTarget
from psi_agent.router.routing import RoutingTarget


def test_routing_target_is_shared_router_target_alias() -> None:
    assert RoutingTarget is RouterTarget
    assert RouterMode("aggregation") is RouterMode.AGGREGATION


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
