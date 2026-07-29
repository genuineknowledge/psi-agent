from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest

from psi_agent.router.client import UpstreamResult
from psi_agent.router.orchestrator import OrchestrationError, Orchestrator
from psi_agent.router.protocol import PlannedTask, RouterConfig


@dataclass
class FakeClient:
    results: dict[str, UpstreamResult | Exception]
    calls: list[str] = field(default_factory=list)
    started: set[str] = field(default_factory=set)
    release: anyio.Event | None = None
    all_started: anyio.Event | None = None

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult:
        self.calls.append(socket)
        self.started.add(socket)
        if self.all_started is not None and len(self.started) == 3:
            self.all_started.set()
        if self.release is not None:
            await self.release.wait()
        result = self.results[socket]
        if isinstance(result, Exception):
            raise result
        return result


@dataclass
class FakePlanner:
    tasks: tuple[PlannedTask, ...] = tuple(PlannedTask(socket=socket, subtask=socket) for socket in ("a", "b", "c"))

    async def plan(self, *, messages: list[dict[str, Any]]) -> tuple[PlannedTask, ...]:
        return self.tasks


def config() -> RouterConfig:
    return RouterConfig(
        session_socket="session",
        router_socket="router",
        default_socket="default",
        upstream=[("a", "A"), ("b", "B"), ("c", "C")],
    )


def body() -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": "task"}], "tools": [], "routing": {"session_id": "s"}}


@pytest.mark.anyio
async def test_fanout_starts_all_upstreams_before_completion() -> None:
    release = anyio.Event()
    client = FakeClient(
        {socket: UpstreamResult(content=socket, finish_reason="stop") for socket in ("a", "b", "c")},
        release=release,
        all_started=anyio.Event(),
    )
    client.results["router"] = UpstreamResult(content="aggregate", finish_reason="stop")
    orchestrator = Orchestrator(config=config(), client=client, planner=FakePlanner())
    async with anyio.create_task_group() as tg:
        result_holder: list[UpstreamResult] = []

        async def run() -> None:
            result_holder.append(await orchestrator.process(body=body()))

        tg.start_soon(run)
        with anyio.fail_after(1):
            assert client.all_started is not None
            await client.all_started.wait()
        release.set()
    assert result_holder[0].content == "aggregate"


@pytest.mark.anyio
async def test_partial_failure_keeps_configured_order() -> None:
    client = FakeClient(
        {
            "a": UpstreamResult(content="A", finish_reason="stop"),
            "b": RuntimeError("boom"),
            "c": UpstreamResult(content="C", finish_reason="stop"),
        }
    )
    client.results["router"] = UpstreamResult(content="aggregate", finish_reason="stop")
    result = await Orchestrator(config=config(), client=client, planner=FakePlanner()).process(body=body())
    assert result.content == "aggregate"


@pytest.mark.anyio
async def test_tool_calls_are_deduplicated_by_id() -> None:
    call = {"id": "same", "type": "function", "function": {"name": "x", "arguments": "{}"}}
    client = FakeClient(
        {socket: UpstreamResult(tool_calls=[call], finish_reason="tool_calls") for socket in ("a", "b", "c")}
    )
    client.results["router"] = UpstreamResult(content="aggregate", finish_reason="stop")
    result = await Orchestrator(config=config(), client=client, planner=FakePlanner()).process(body=body())
    assert len(result.tool_calls) == 0


@pytest.mark.anyio
async def test_all_upstreams_failure_raises() -> None:
    client = FakeClient({socket: RuntimeError(socket) for socket in ("a", "b", "c")})
    with pytest.raises(OrchestrationError):
        await Orchestrator(config=config(), client=client, planner=FakePlanner()).process(body=body())
