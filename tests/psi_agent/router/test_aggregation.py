from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from psi_agent.router.aggregation import AggregationOrchestrator
from psi_agent.router.client import UpstreamResult
from psi_agent.router.protocol import PlannedTask, RouterConfig


def test_aggregation_module_exports_aggregation_orchestrator() -> None:
    assert AggregationOrchestrator.__name__ == "AggregationOrchestrator"


@dataclass
class FakeAggregationClient:
    calls: list[dict[str, Any]]

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult:
        del socket, options
        self.calls.append(body)
        if len(self.calls) == 1:
            return UpstreamResult(
                content="subtask answer",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            )
        return UpstreamResult(content="final aggregation", finish_reason="stop")


@dataclass
class FakeAggregationPlanner:
    tasks: tuple[PlannedTask, ...]

    async def plan(
        self, *, messages: list[dict[str, Any]], max_context_length: int | None = None
    ) -> tuple[PlannedTask, ...]:
        del messages, max_context_length
        return self.tasks


def _aggregation_config() -> RouterConfig:
    return RouterConfig(
        mode="aggregation",
        session_socket="session",
        router_socket="router",
        default_socket="default",
        upstream=[("a", "A")],
    )


@pytest.mark.anyio
async def test_aggregation_passes_child_tool_calls_to_final_prompt_and_returns_only_final_result() -> None:
    client = FakeAggregationClient(calls=[])
    orchestrator = AggregationOrchestrator(
        config=_aggregation_config(),
        client=client,
        planner=FakeAggregationPlanner(tasks=(PlannedTask(subtask="do the thing", socket="a"),)),
    )

    result = await orchestrator.process(body={"messages": [{"role": "user", "content": "task"}], "tools": []})

    assert result.content == "final aggregation"
    assert result.finish_reason == "stop"
    assert len(client.calls) == 2
    assert client.calls[1]["messages"][-1]["content"]
    assert "call-1" in client.calls[1]["messages"][-1]["content"]
    assert "lookup" in client.calls[1]["messages"][-1]["content"]
    assert result.tool_calls == []
