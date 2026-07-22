from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.client import UpstreamResult
from psi_agent.router.orchestrator import OrchestrationError, Orchestrator
from psi_agent.router.protocol import PlannedTask, RouterConfig


@dataclass
class FakePlanner:
    tasks: tuple[PlannedTask, PlannedTask, PlannedTask]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    async def plan(self, *, messages: list[dict[str, Any]]) -> tuple[PlannedTask, PlannedTask, PlannedTask]:
        self.calls.append(messages)
        return self.tasks


@dataclass
class FakeClient:
    results: list[UpstreamResult]
    calls: list[tuple[str, dict[str, Any], float | None]] = field(default_factory=list)

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult:
        timeout = options.get("timeout")
        assert timeout is None or (isinstance(timeout, (int, float)) and not isinstance(timeout, bool))
        self.calls.append((socket, body, timeout))
        return self.results.pop(0)


def _config(*, max_tool_rounds: int = 10) -> RouterConfig:
    return RouterConfig(
        session_socket="session",
        router_socket="router",
        default_socket="default",
        upstream=[("branch-1", "first"), ("branch-2", "second"), ("branch-3", "third")],
        max_tool_rounds=max_tool_rounds,
        branch_timeout=12.0,
        aggregate_timeout=24.0,
    )


def _planner() -> FakePlanner:
    return FakePlanner(
        tasks=(
            PlannedTask(subtask="collect facts", socket="branch-1"),
            PlannedTask(subtask="analyze facts", socket="branch-2"),
            PlannedTask(subtask="check conclusions", socket="branch-3"),
        )
    )


def _body(*, session_id: str = "session-a", messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "messages": messages or [{"role": "user", "content": "Investigate the question"}],
        "tools": [
            {"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "calculate", "parameters": {"type": "object"}}},
        ],
        "temperature": 0.3,
        "routing": {"session_id": session_id},
        "model": "internal-selection-metadata",
    }


def _tool_call(call_id: str, *, name: str, arguments: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.mark.anyio
async def test_process_runs_branches_serially_with_only_prior_final_answers_and_full_tools() -> None:
    planner = _planner()
    client = FakeClient(
        results=[
            UpstreamResult(content="answer one", reasoning="private reasoning one", finish_reason="stop"),
            UpstreamResult(content="answer two", reasoning="private reasoning two", finish_reason="stop"),
            UpstreamResult(content="answer three", reasoning="private reasoning three", finish_reason="stop"),
            UpstreamResult(content="combined answer", finish_reason="stop"),
        ]
    )
    orchestrator = Orchestrator(config=_config(), client=client, planner=planner)
    body = _body()

    result = await orchestrator.process(body=body)

    assert result == UpstreamResult(content="combined answer", finish_reason="stop")
    assert [socket for socket, _, _ in client.calls] == ["branch-1", "branch-2", "branch-3", "router"]
    branch_bodies = [call_body for _, call_body, _ in client.calls[:3]]
    assert all(call_body["tools"] == body["tools"] for call_body in branch_bodies)
    assert all(call_body["temperature"] == 0.3 for call_body in branch_bodies)
    assert all("routing" not in call_body and "model" not in call_body for call_body in branch_bodies)

    branch_1_context = str(branch_bodies[0]["messages"])
    branch_2_context = str(branch_bodies[1]["messages"])
    branch_3_context = str(branch_bodies[2]["messages"])
    assert "answer one" not in branch_1_context
    assert "answer one" in branch_2_context
    assert "answer two" not in branch_2_context
    assert "answer one" in branch_3_context
    assert "answer two" in branch_3_context
    assert "private reasoning" not in branch_2_context
    assert "private reasoning" not in branch_3_context

    aggregation_body = client.calls[3][1]
    assert "tools" not in aggregation_body
    assert "answer one" in str(aggregation_body["messages"])
    assert "answer two" in str(aggregation_body["messages"])
    assert "answer three" in str(aggregation_body["messages"])
    assert client.calls[3][2] == 24.0
    assert orchestrator.runs == {}


@pytest.mark.anyio
async def test_process_supports_multiple_tool_rounds_with_global_ids_and_restored_upstream_ids() -> None:
    planner = _planner()
    client = FakeClient(
        results=[
            UpstreamResult(
                content="private first call",
                reasoning="hidden",
                tool_calls=[_tool_call("local-id", name="search", arguments='{"q":"first"}')],
                finish_reason="tool_calls",
            ),
            UpstreamResult(
                tool_calls=[_tool_call("local-id", name="calculate", arguments='{"x":2}')],
                finish_reason="tool_calls",
            ),
            UpstreamResult(content="answer one", finish_reason="stop"),
            UpstreamResult(content="answer two", finish_reason="stop"),
            UpstreamResult(content="answer three", finish_reason="stop"),
            UpstreamResult(content="combined answer", finish_reason="stop"),
        ]
    )
    orchestrator = Orchestrator(config=_config(), client=client, planner=planner)

    first = await orchestrator.process(body=_body())
    first_global_id = first.tool_calls[0]["id"]
    assert first.finish_reason == "tool_calls"
    assert first.content == "Processing subtask 1: collect facts"
    assert first_global_id != "local-id"
    assert set(orchestrator.runs["session-a"].pending_tool_calls) == {first_global_id}

    second = await orchestrator.process(
        body=_body(
            messages=[
                {"role": "user", "content": "Investigate the question"},
                {"role": "assistant", "content": first.content, "tool_calls": first.tool_calls},
                {"role": "tool", "tool_call_id": first_global_id, "content": "raw first result"},
            ]
        )
    )
    second_global_id = second.tool_calls[0]["id"]
    assert second.finish_reason == "tool_calls"
    assert second_global_id not in {"local-id", first_global_id}
    second_branch_request = client.calls[1][1]
    assert second_branch_request["messages"][-2]["tool_calls"][0]["id"] == "local-id"
    assert second_branch_request["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "local-id",
        "content": "raw first result",
    }

    final = await orchestrator.process(
        body=_body(
            messages=[
                {"role": "user", "content": "Investigate the question"},
                {"role": "assistant", "content": second.content, "tool_calls": second.tool_calls},
                {"role": "tool", "tool_call_id": second_global_id, "content": "raw second result"},
            ]
        )
    )

    assert final.content == "combined answer"
    third_branch_request = client.calls[2][1]
    assert third_branch_request["messages"][-2]["tool_calls"][0]["id"] == "local-id"
    assert third_branch_request["messages"][-1]["tool_call_id"] == "local-id"
    assert orchestrator.runs == {}


@pytest.mark.anyio
async def test_process_uses_distinct_global_tool_ids_across_branches() -> None:
    client = FakeClient(
        results=[
            UpstreamResult(
                tool_calls=[_tool_call("same-local-id", name="search", arguments="{}")],
                finish_reason="tool_calls",
            ),
            UpstreamResult(content="answer one", finish_reason="stop"),
            UpstreamResult(
                tool_calls=[_tool_call("same-local-id", name="search", arguments="{}")],
                finish_reason="tool_calls",
            ),
        ]
    )
    orchestrator = Orchestrator(config=_config(), client=client, planner=_planner())
    first = await orchestrator.process(body=_body())
    first_global_id = first.tool_calls[0]["id"]

    second = await orchestrator.process(
        body=_body(
            messages=[
                {"role": "assistant", "content": first.content, "tool_calls": first.tool_calls},
                {"role": "tool", "tool_call_id": first_global_id, "content": "first result"},
            ]
        )
    )

    assert second.finish_reason == "tool_calls"
    assert second.tool_calls[0]["id"] != first_global_id
    assert [socket for socket, _, _ in client.calls] == ["branch-1", "branch-1", "branch-2"]


@pytest.mark.anyio
async def test_process_rejects_mismatched_tool_results_and_deletes_run() -> None:
    client = FakeClient(
        results=[
            UpstreamResult(
                tool_calls=[_tool_call("local-id", name="search", arguments="{}")],
                finish_reason="tool_calls",
            )
        ]
    )
    orchestrator = Orchestrator(config=_config(), client=client, planner=_planner())
    first = await orchestrator.process(body=_body())

    with pytest.raises(OrchestrationError, match="pending tool calls"):
        await orchestrator.process(
            body=_body(
                messages=[
                    {"role": "assistant", "content": first.content, "tool_calls": first.tool_calls},
                    {"role": "tool", "tool_call_id": "unrelated-id", "content": "wrong result"},
                ]
            )
        )

    assert len(client.calls) == 1
    assert orchestrator.runs == {}


@pytest.mark.anyio
async def test_process_rejects_a_tool_round_beyond_the_configured_limit() -> None:
    client = FakeClient(
        results=[
            UpstreamResult(
                tool_calls=[_tool_call("first-local", name="search", arguments="{}")],
                finish_reason="tool_calls",
            ),
            UpstreamResult(
                tool_calls=[_tool_call("second-local", name="search", arguments="{}")],
                finish_reason="tool_calls",
            ),
        ]
    )
    orchestrator = Orchestrator(config=_config(max_tool_rounds=1), client=client, planner=_planner())
    first = await orchestrator.process(body=_body())

    with pytest.raises(OrchestrationError, match="maximum tool rounds"):
        await orchestrator.process(
            body=_body(
                messages=[
                    {"role": "assistant", "content": first.content, "tool_calls": first.tool_calls},
                    {"role": "tool", "tool_call_id": first.tool_calls[0]["id"], "content": "result"},
                ]
            )
        )

    assert orchestrator.runs == {}
