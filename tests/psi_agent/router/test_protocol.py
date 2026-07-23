from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from psi_agent.router.protocol import (
    BranchState,
    BranchStatus,
    PlannedTask,
    RouterConfig,
    RoutingRun,
    RoutingStatus,
)


def _config(*, upstream: object = [("socket-a", "research")], **overrides: Any) -> RouterConfig:
    values: dict[str, Any] = {
        "session_socket": "router.sock",
        "router_socket": "planner.sock",
        "default_socket": "fallback.sock",
        "upstream": upstream,
    }
    values.update(overrides)
    return RouterConfig(**values)


def test_router_config_accepts_list_of_socket_description_tuples() -> None:
    config = _config(upstream=[("socket-a", "research"), ("socket-b", "writing")])

    assert config.upstream == (("socket-a", "research"), ("socket-b", "writing"))


def test_router_config_accepts_tuple_of_socket_description_tuples() -> None:
    config = _config(upstream=(("socket-a", "research"),))

    assert config.upstream == (("socket-a", "research"),)


@pytest.mark.parametrize(
    "upstream",
    [
        [],
        "socket-a",
        [("socket-a",)],
        [("socket-a", 1)],
        [("", "research")],
        [("socket-a", "")],
    ],
)
def test_router_config_rejects_invalid_upstream(upstream: object) -> None:
    with pytest.raises(ValueError, match="upstream"):
        _config(upstream=upstream)


def test_router_config_allows_duplicate_upstream_sockets() -> None:
    config = _config(upstream=[("socket-a", "research"), ("socket-a", "analysis")])

    assert config.upstream == (("socket-a", "research"), ("socket-a", "analysis"))


def test_router_config_is_immutable() -> None:
    config = _config()

    with pytest.raises(FrozenInstanceError):
        object.__setattr__(config, "default_socket", "other.sock")


def test_router_config_rejects_default_socket_matching_session_socket() -> None:
    with pytest.raises(ValueError, match=r"default_socket.*session_socket"):
        _config(default_socket="router.sock")


def test_routing_run_has_exactly_three_branches() -> None:
    branch = BranchState(subtask="one", socket="socket-a", messages=[])

    with pytest.raises(ValueError, match="exactly three"):
        RoutingRun.create(
            run_id="run",
            session_id="session",
            original_messages=[],
            tools=[],
            branches=[branch],
        )


def test_routing_run_rejects_the_same_mutable_branch_three_times() -> None:
    branch = BranchState(subtask="one", socket="socket-a")

    with pytest.raises(ValueError, match="distinct"):
        RoutingRun.create(
            run_id="run",
            session_id="session",
            original_messages=[],
            tools=[],
            branches=[branch, branch, branch],
        )


def test_branch_state_messages_use_an_independent_mutable_default() -> None:
    first = BranchState(subtask="one", socket="socket-a")
    second = BranchState(subtask="two", socket="socket-b")

    first.messages.append({"role": "user", "content": "first"})

    assert second.messages == []


def test_routing_run_activates_only_first_of_three_branches() -> None:
    tasks = tuple(PlannedTask(subtask=str(index), socket="socket-a") for index in range(3))

    run = RoutingRun.create(
        run_id="run",
        session_id="session",
        original_messages=[],
        tools=[],
        branches=[BranchState.from_task(task) for task in tasks],
    )

    assert [branch.socket for branch in run.branches] == ["socket-a"] * 3
    assert [branch.status for branch in run.branches] == [
        BranchStatus.READY,
        BranchStatus.PENDING,
        BranchStatus.PENDING,
    ]
    assert run.status is RoutingStatus.RUNNING


def test_branch_and_run_statuses_are_limited_to_protocol_values() -> None:
    assert set(BranchStatus) == {
        BranchStatus.PENDING,
        BranchStatus.READY,
        BranchStatus.WAITING_TOOLS,
        BranchStatus.COMPLETED,
        BranchStatus.FAILED,
    }
    assert set(RoutingStatus) == {
        RoutingStatus.PLANNING,
        RoutingStatus.RUNNING,
        RoutingStatus.AGGREGATING,
        RoutingStatus.COMPLETED,
        RoutingStatus.FAILED,
    }
