from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.aggregation.planner import Planner, PlanValidationError, parse_plan
from psi_agent.router.client import UpstreamResult


@pytest.mark.parametrize(
    ("content", "allowed_sockets"),
    [
        (
            '{"tasks":[{"subtask":"research","socket":"a"},{"subtask":"analyze","socket":"a"},{"subtask":"review","socket":"b"}]}',
            {"a", "b"},
        ),
        (
            '```json\n{"tasks":[{"subtask":"research","socket":"a"},{"subtask":"analyze","socket":"a"},{"subtask":"review","socket":"b"}]}\n```',
            {"a", "b"},
        ),
    ],
)
def test_parse_plan_rejects_repeated_sockets(content: str, allowed_sockets: set[str]) -> None:
    with pytest.raises(PlanValidationError, match="more than once"):
        parse_plan(content, allowed_sockets=allowed_sockets)


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"tasks":[{"subtask":"","socket":"a"},{"subtask":"two","socket":"a"},{"subtask":"three","socket":"a"}]}',
        '{"tasks":[{"subtask":"one","socket":1},{"subtask":"two","socket":"a"},{"subtask":"three","socket":"a"}]}',
        '{"tasks":[{"subtask":"one","socket":"missing"},{"subtask":"two","socket":"a"},{"subtask":"three","socket":"a"}]}',
    ],
)
def test_parse_plan_rejects_invalid_structures(content: str) -> None:
    with pytest.raises(PlanValidationError):
        parse_plan(content, allowed_sockets={"a"})


@pytest.mark.parametrize(
    "content",
    [
        '{"tasks":[{"subtask":"one","socket":"a"},{"subtask":"two","socket":"a"},{"subtask":"three","socket":"a"}],"extra":true}',
        '{"tasks":[{"subtask":"one","socket":"a","extra":true},{"subtask":"two","socket":"a"},{"subtask":"three","socket":"a"}]}',
    ],
)
def test_parse_plan_rejects_extra_root_or_task_keys(content: str) -> None:
    with pytest.raises(PlanValidationError):
        parse_plan(content, allowed_sockets={"a"})


def test_parse_plan_strips_subtask_and_socket_before_mapping() -> None:
    tasks = parse_plan(
        '{"tasks":[{"subtask":" one ","socket":" a "},'
        '{"subtask":" two ","socket":" b "},{"subtask":" three ","socket":" c "}]}',
        allowed_sockets={"a", "b", "c"},
    )

    assert [(task.subtask, task.socket) for task in tasks] == [("one", "a"), ("two", "b"), ("three", "c")]


@dataclass
class FakeClient:
    results: list[UpstreamResult]
    calls: list[tuple[str, dict[str, Any], float | None]] = field(default_factory=list)

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult:
        timeout = options.get("timeout")
        assert timeout is None or (isinstance(timeout, (int, float)) and not isinstance(timeout, bool))
        self.calls.append((socket, body, timeout))
        return self.results.pop(0)


@pytest.mark.anyio
async def test_planner_fails_immediately_on_invalid_result() -> None:
    client = FakeClient(
        results=[
            UpstreamResult(content="not JSON", finish_reason="stop"),
            UpstreamResult(
                content='{"tasks":[{"subtask":"one","socket":"a"},{"subtask":"two","socket":"a"},{"subtask":"three","socket":"a"}]}',
                finish_reason="stop",
            ),
        ]
    )
    planner = Planner(client=client, router_socket="router", upstream=[("a", "research")], timeout=12.0)

    with pytest.raises(PlanValidationError):
        await planner.plan(messages=[{"role": "user", "content": "Investigate"}])

    assert [socket for socket, _, _ in client.calls] == ["router"]
    assert [timeout for _, _, timeout in client.calls] == [12.0]
    assert "request type" in client.calls[0][1]["messages"][-1]["content"]


@pytest.mark.anyio
async def test_planner_raises_without_repair_attempt() -> None:
    client = FakeClient(
        results=[
            UpstreamResult(content="not JSON", finish_reason="stop"),
            UpstreamResult(content="still not JSON", finish_reason="stop"),
        ]
    )
    planner = Planner(client=client, router_socket="router", upstream=[("a", "research")], timeout=None)

    with pytest.raises(PlanValidationError):
        await planner.plan(messages=[])

    assert [socket for socket, _, _ in client.calls] == ["router"]
