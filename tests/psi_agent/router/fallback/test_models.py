from __future__ import annotations

import math
from typing import cast

import pytest

from psi_agent.router.fallback import FallbackConfig
from psi_agent.router.models import RouterTarget


def _target(candidate_id: str = "candidate-1", socket: str = "target-1.sock") -> RouterTarget:
    return RouterTarget(candidate_id, socket, "general")


def test_fallback_config_normalizes_fields_and_targets() -> None:
    config = FallbackConfig(
        session_socket=" router.sock ",
        targets=[_target()],
        target_timeout=5,
    )

    assert config.session_socket == "router.sock"
    assert config.targets == (_target(),)
    assert config.target_timeout == 5


@pytest.mark.parametrize(
    ("session_socket", "targets", "match"),
    [
        ("", [_target()], "session_socket"),
        ("router.sock", [], "targets"),
        ("router.sock", [cast(RouterTarget, "not-a-target")], "RouterTarget"),
        ("router.sock", [_target(socket="router.sock")], "session_socket"),
        ("router.sock", [_target(), _target()], "candidate_id"),
        (
            "router.sock",
            [_target(), _target(candidate_id="candidate-2")],
            "socket",
        ),
    ],
)
def test_fallback_config_rejects_invalid_targets(
    session_socket: str,
    targets: list[RouterTarget],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        FallbackConfig(session_socket=session_socket, targets=targets)


@pytest.mark.parametrize("timeout", [0, -1, math.inf, -math.inf, math.nan, True, "30"])
def test_fallback_config_rejects_invalid_timeout(timeout: object) -> None:
    with pytest.raises(ValueError, match="target_timeout"):
        FallbackConfig(
            session_socket="router.sock",
            targets=[_target()],
            target_timeout=cast(float | None, timeout),
        )
