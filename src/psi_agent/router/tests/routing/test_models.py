from __future__ import annotations

import pytest

from psi_agent.router.routing.models import RoutingConfig, RoutingTarget


def _target(candidate_id: str = "code", socket: str = "code.sock") -> RoutingTarget:
    return RoutingTarget(candidate_id=candidate_id, socket=socket, description="coding specialist")


def test_config_normalizes_targets_and_addresses() -> None:
    config = RoutingConfig(
        session_socket="  router.sock ",
        selector_socket=" selector.sock ",
        targets=[_target()],
    )

    assert config.session_socket == "router.sock"
    assert config.selector_socket == "selector.sock"
    assert config.targets == (_target(),)


@pytest.mark.parametrize(
    ("candidate_id", "socket", "description"),
    [
        ("bad id", "target.sock", "valid"),
        ("valid", "", "valid"),
        ("valid", "target.sock", ""),
    ],
)
def test_target_rejects_invalid_fields(candidate_id: str, socket: str, description: str) -> None:
    with pytest.raises(ValueError):
        RoutingTarget(candidate_id=candidate_id, socket=socket, description=description)


def test_config_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValueError, match="candidate_id"):
        RoutingConfig(
            session_socket="router.sock",
            selector_socket="selector.sock",
            targets=[_target(socket="a.sock"), _target(socket="b.sock")],
        )


def test_config_rejects_duplicate_target_sockets() -> None:
    with pytest.raises(ValueError, match="socket values"):
        RoutingConfig(
            session_socket="router.sock",
            selector_socket="selector.sock",
            targets=[_target(), _target(candidate_id="chat")],
        )


@pytest.mark.parametrize("self_reference", ["selector", "target"])
def test_config_rejects_direct_self_reference(self_reference: str) -> None:
    selector_socket = "router.sock" if self_reference == "selector" else "selector.sock"
    target_socket = "router.sock" if self_reference == "target" else "target.sock"
    with pytest.raises(ValueError, match="session_socket"):
        RoutingConfig(
            session_socket="router.sock",
            selector_socket=selector_socket,
            targets=[_target(socket=target_socket)],
        )
