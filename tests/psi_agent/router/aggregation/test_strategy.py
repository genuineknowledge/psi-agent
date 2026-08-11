"""Behavior contracts for parallel broadcast aggregation."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from typing import Any

import anyio
import pytest

from psi_agent._router_status import RouterStatus, router_status_from_event
from psi_agent.router.aggregation import AggregationConfig, AggregationError, AggregationStrategy
from psi_agent.router.errors import RouterUpstreamError
from psi_agent.router.models import CompletionResult, RouterTarget

_TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


def _aggregation_feedback(content: str) -> list[dict[str, Any]]:
    serialized = content.split("<aggregation_feedback_json>\n", maxsplit=1)[1].split(
        "\n</aggregation_feedback_json>", maxsplit=1
    )[0]
    value = json.loads(serialized)["aggregation_feedback"]
    assert isinstance(value, list)
    return value


class FakeAggregationClient:
    def __init__(
        self,
        *,
        results: dict[str, CompletionResult | Exception] | None = None,
        aggregator_events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.results = results or {}
        self.aggregator_events = aggregator_events or [
            {"choices": [{"index": 0, "delta": {"content": "combined"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        self.complete_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.complete_snapshots: dict[str, dict[str, Any]] = {}
        self.started: set[str] = set()
        self.started_all = anyio.Event()
        self.finished: dict[str, anyio.Event] = {}
        self.releases: dict[str, anyio.Event] = {}
        self.expected_branches = len(self.results)
        self.exited_branches = 0
        self.all_branches_exited = anyio.Event()
        self.aggregator_body: dict[str, Any] | None = None
        self.aggregator_socket: str | None = None
        self.aggregator_options: dict[str, Any] | None = None
        self.aggregator_called = anyio.Event()
        self.aggregator_release: anyio.Event | None = None
        self.aggregator_closed = anyio.Event()
        self.stream_error: Exception | None = None
        self.mutate_when_streaming: dict[str, Any] | None = None

    async def complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CompletionResult:
        self.complete_calls.append((socket, body, options))
        self.complete_snapshots[socket] = deepcopy(body)
        self.started.add(socket)
        if len(self.started) == self.expected_branches:
            self.started_all.set()
        finished = self.finished.setdefault(socket, anyio.Event())
        try:
            release = self.releases.get(socket)
            if release is not None:
                await release.wait()
            result = self.results[socket]
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            finished.set()
            self.exited_branches += 1
            if self.exited_branches == self.expected_branches:
                self.all_branches_exited.set()

    async def stream(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any]]:
        self.aggregator_socket = socket
        self.aggregator_body = body
        self.aggregator_options = options
        self.aggregator_called.set()
        try:
            if self.mutate_when_streaming is not None:
                self.mutate_when_streaming["function"]["arguments"] = "mutated"
            if self.stream_error is not None:
                raise self.stream_error
            for index, event in enumerate(self.aggregator_events):
                yield event
                if index == 0 and self.aggregator_release is not None:
                    await self.aggregator_release.wait()
        finally:
            self.aggregator_closed.set()


def _targets(count: int = 3) -> list[RouterTarget]:
    return [
        RouterTarget(f"candidate-{index}", f"private-{index}.sock", f"description-{index}")
        for index in range(1, count + 1)
    ]


def _config(targets: list[RouterTarget], **changes: Any) -> AggregationConfig:
    values: dict[str, Any] = {
        "session_socket": "router.sock",
        "aggregator_socket": "aggregate.sock",
        "targets": targets,
        "aggregator_timeout": 9,
        "target_timeout": 4,
    }
    values.update(changes)
    return AggregationConfig(**values)


def _body() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "solve"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "future_parameter": {"preserved": True},
        "stream": True,
        "model": "private-model",
        "routing": {"session_id": "private-session", "trace_id": _TRACE_ID},
    }


async def _collect(stream: AsyncGenerator[dict[str, Any]]) -> list[dict[str, Any]]:
    async with aclosing(stream) as events:
        return [event async for event in events]


def _statuses(events: list[dict[str, Any]]) -> list[RouterStatus]:
    return [status for event in events if (status := router_status_from_event(event)) is not None]


def _completion_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if router_status_from_event(event) is None]


@pytest.mark.anyio
async def test_partial_failure_builds_ordered_sanitized_feedback_and_calls_aggregator() -> None:
    targets = _targets()
    client = FakeAggregationClient(
        results={
            targets[0].socket: CompletionResult(content="answer one", finish_reason="stop"),
            targets[1].socket: CompletionResult(content="answer two", finish_reason="stop"),
            targets[2].socket: RouterUpstreamError(f"{targets[2].socket} returned HTTP 503"),
        }
    )
    client.releases = {target.socket: anyio.Event() for target in targets}
    strategy = AggregationStrategy(config=_config(targets), client=client)
    collected: list[list[dict[str, Any]]] = []

    async def consume() -> None:
        collected.append(await _collect(strategy.stream(body=_body())))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        await client.started_all.wait()
        for target in reversed(targets):
            client.releases[target.socket].set()
            await client.finished[target.socket].wait()

    completion_events = _completion_events(collected[0])
    assert completion_events[0]["choices"][0]["delta"]["content"] == "combined"
    assert collected[0][-1]["choices"][0]["finish_reason"] == "stop"
    statuses = _statuses(collected[0])
    assert [(status.phase, status.completed, status.total, status.degraded) for status in statuses] == [
        ("collecting", 0, 3, False),
        ("synthesizing", 3, 3, True),
    ]
    assert all(status.trace_id == _TRACE_ID for status in statuses)
    assert client.aggregator_socket == "aggregate.sock"
    assert client.aggregator_body is not None
    feedback = _aggregation_feedback(client.aggregator_body["messages"][-1]["content"])
    assert [item["candidate_id"] for item in feedback] == [
        "candidate-1",
        "candidate-2",
        "candidate-3",
    ]
    assert feedback[2]["status"] == "error"
    assert "private-3.sock" not in client.aggregator_body["messages"][-1]["content"]
    assert "<private-socket>" in client.aggregator_body["messages"][-1]["content"]


@pytest.mark.anyio
async def test_strict_aggregation_rejects_partial_failure_before_synthesis() -> None:
    targets = _targets(2)
    client = FakeAggregationClient(
        results={
            targets[0].socket: CompletionResult(content="usable", finish_reason="stop"),
            targets[1].socket: RouterUpstreamError("failed"),
        }
    )
    strategy = AggregationStrategy(
        config=_config(targets, require_all_targets=True),
        client=client,
    )

    with pytest.raises(AggregationError, match="requires every target"):
        await _collect(strategy.stream(body=_body()))

    assert not client.aggregator_called.is_set()


@pytest.mark.anyio
async def test_strict_aggregation_rejects_incomplete_branch_finish() -> None:
    targets = _targets(2)
    client = FakeAggregationClient(
        results={
            targets[0].socket: CompletionResult(content="complete", finish_reason="stop"),
            targets[1].socket: CompletionResult(content="truncated", finish_reason="length"),
        }
    )
    strategy = AggregationStrategy(
        config=_config(targets, require_all_targets=True),
        client=client,
    )

    with pytest.raises(AggregationError, match="requires every target"):
        await _collect(strategy.stream(body=_body()))

    assert not client.aggregator_called.is_set()


@pytest.mark.anyio
async def test_non_strict_aggregation_marks_incomplete_branch_as_degraded() -> None:
    targets = _targets(2)
    client = FakeAggregationClient(
        results={
            targets[0].socket: CompletionResult(content="complete", finish_reason="stop"),
            targets[1].socket: CompletionResult(content="truncated", finish_reason="length"),
        }
    )

    events = await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert _statuses(events)[-1].phase == "synthesizing"
    assert _statuses(events)[-1].degraded is True


@pytest.mark.anyio
async def test_fanout_starts_every_upstream_before_any_branch_is_released() -> None:
    targets = _targets()
    client = FakeAggregationClient(
        results={
            target.socket: CompletionResult(content=target.candidate_id, finish_reason="stop") for target in targets
        }
    )
    client.releases = {target.socket: anyio.Event() for target in targets}
    strategy = AggregationStrategy(config=_config(targets), client=client)
    completed = anyio.Event()

    async def consume() -> None:
        await _collect(strategy.stream(body=_body()))
        completed.set()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        with anyio.fail_after(2):
            await client.started_all.wait()
        assert client.started == {target.socket for target in targets}
        assert not completed.is_set()
        for release in client.releases.values():
            release.set()


@pytest.mark.anyio
async def test_each_upstream_gets_an_equal_but_independent_public_request_copy() -> None:
    targets = _targets(2)
    client = FakeAggregationClient(
        results={
            target.socket: CompletionResult(content=target.candidate_id, finish_reason="stop") for target in targets
        }
    )
    source = _body()
    strategy = AggregationStrategy(config=_config(targets), client=client)

    await _collect(strategy.stream(body=source))

    expected = {
        "messages": [{"role": "user", "content": "solve"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "future_parameter": {"preserved": True},
        "stream": True,
    }
    assert set(client.complete_snapshots) == {target.socket for target in targets}
    assert all(snapshot == expected for snapshot in client.complete_snapshots.values())
    first_body = client.complete_calls[0][1]
    second_body = client.complete_calls[1][1]
    assert first_body is not second_body
    assert first_body["messages"] is not second_body["messages"]
    first_body["messages"][0]["content"] = "changed"
    assert second_body["messages"][0]["content"] == "solve"
    assert source["messages"][0]["content"] == "solve"


@pytest.mark.anyio
async def test_candidate_timeout_overrides_aggregation_target_timeout_per_branch() -> None:
    targets = [
        RouterTarget("candidate-1", "private-1.sock", "one", timeout=2.5),
        RouterTarget("candidate-2", "private-2.sock", "two"),
    ]
    client = FakeAggregationClient(
        results={
            target.socket: CompletionResult(content=target.candidate_id, finish_reason="stop") for target in targets
        }
    )

    await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    options_by_socket = {socket: options for socket, _, options in client.complete_calls}
    assert options_by_socket == {
        "private-1.sock": {"timeout": 2.5, "trace_id": _TRACE_ID},
        "private-2.sock": {"timeout": 4, "trace_id": _TRACE_ID},
    }


@pytest.mark.anyio
async def test_aggregator_body_replaces_only_messages_and_preserves_public_parameters() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="branch", finish_reason="stop")}
    )

    await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert client.aggregator_body is not None
    assert client.aggregator_options == {"timeout": 9, "trace_id": _TRACE_ID}
    assert client.aggregator_body["messages"][0]["role"] == "system"
    assert client.aggregator_body["messages"][1:-1] == _body()["messages"]
    assert client.aggregator_body["messages"] != _body()["messages"]
    assert {key: value for key, value in client.aggregator_body.items() if key != "messages"} == {
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "future_parameter": {"preserved": True},
        "stream": True,
    }


@pytest.mark.anyio
async def test_target_and_aggregator_request_overrides_apply_with_shallow_precedence() -> None:
    target_overrides: dict[str, Any] = {
        "temperature": 0.7,
        "max_tokens": 4096,
        "future_parameter": {"role": "target"},
        "detached": {"values": ["target-original"]},
    }
    aggregator_overrides: dict[str, Any] = {
        "temperature": 0.4,
        "max_tokens": 2048,
        "future_parameter": {"role": "aggregator"},
        "detached": {"values": ["aggregator-original"]},
    }
    target = RouterTarget(
        "candidate-1",
        "private-1.sock",
        "description-1",
        request_overrides=target_overrides,
    )
    config = _config([target], aggregator_request_overrides=aggregator_overrides)
    client = FakeAggregationClient(results={target.socket: CompletionResult(content="branch", finish_reason="stop")})
    target_overrides["detached"]["values"].append("mutated")
    aggregator_overrides["detached"]["values"].append("mutated")

    await _collect(AggregationStrategy(config=config, client=client).stream(body=_body()))

    target_body = client.complete_snapshots[target.socket]
    assert target_body["temperature"] == 0.7
    assert target_body["max_tokens"] == 4096
    assert target_body["future_parameter"] == {"role": "target"}
    assert target_body["detached"] == {"values": ["target-original"]}
    assert target_body["stream"] is True
    assert client.aggregator_body is not None
    assert client.aggregator_body["temperature"] == 0.4
    assert client.aggregator_body["max_tokens"] == 2048
    assert client.aggregator_body["future_parameter"] == {"role": "aggregator"}
    assert client.aggregator_body["detached"] == {"values": ["aggregator-original"]}
    assert client.aggregator_body["stream"] is True
    assert client.aggregator_body["messages"] != _body()["messages"]


@pytest.mark.anyio
async def test_branch_reasoning_is_dropped_and_branch_tool_calls_are_feedback_only() -> None:
    targets = _targets(1)
    branch_tool = {
        "id": "branch-call",
        "type": "function",
        "function": {"name": "lookup", "arguments": "original"},
    }
    client = FakeAggregationClient(
        results={
            targets[0].socket: CompletionResult(
                reasoning="private chain of thought",
                tool_calls=[branch_tool],
                finish_reason="tool_calls",
            )
        }
    )
    client.mutate_when_streaming = branch_tool

    events = await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert client.aggregator_body is not None
    serialized = client.aggregator_body["messages"][-1]["content"]
    feedback = _aggregation_feedback(serialized)[0]
    assert "private chain of thought" not in serialized
    assert feedback["tool_calls"][0]["function"]["arguments"] == "original"
    assert branch_tool["function"]["arguments"] == "mutated"
    assert all("tool_calls" not in event["choices"][0]["delta"] for event in events)


@pytest.mark.anyio
async def test_branch_error_summary_replaces_every_private_socket_and_caps_at_512_characters() -> None:
    targets = _targets(2)
    private_text = f"{targets[0].socket} {targets[1].socket} aggregate.sock " + "x" * 800
    client = FakeAggregationClient(
        results={
            targets[0].socket: RouterUpstreamError(private_text),
            targets[1].socket: CompletionResult(content="usable", finish_reason="stop"),
        }
    )

    await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert client.aggregator_body is not None
    feedback = _aggregation_feedback(client.aggregator_body["messages"][-1]["content"])[0]
    assert len(feedback["error"]) == 512
    assert "<private-socket>" in feedback["error"]
    assert all(socket not in feedback["error"] for socket in [targets[0].socket, targets[1].socket, "aggregate.sock"])


@pytest.mark.anyio
async def test_branch_error_summary_sanitizes_windows_named_pipe_repr_forms() -> None:
    targets = [
        RouterTarget("candidate-1", r"\\.\pipe\private", "one"),
        RouterTarget("candidate-2", r"\\.\pipe\private-one", "two"),
    ]
    aggregator_socket = r"\\.\pipe\aggregate"
    private_sockets = [target.socket for target in targets] + [aggregator_socket]
    private_representations = [
        representation for socket in private_sockets for representation in (socket, repr(socket), repr(socket)[1:-1])
    ]
    real_client_error = " ".join(private_representations)
    client = FakeAggregationClient(
        results={
            targets[0].socket: RouterUpstreamError(f"Upstream {real_client_error} returned HTTP 503"),
            targets[1].socket: CompletionResult(content="usable", finish_reason="stop"),
        }
    )

    await _collect(
        AggregationStrategy(
            config=_config(targets, aggregator_socket=aggregator_socket),
            client=client,
        ).stream(body=_body())
    )

    assert client.aggregator_body is not None
    error = _aggregation_feedback(client.aggregator_body["messages"][-1]["content"])[0]["error"]
    assert "<private-socket>" in error
    assert error == (
        "Upstream <private-socket> <private-socket> <private-socket> "
        "<private-socket> <private-socket> <private-socket> "
        "<private-socket> <private-socket> <private-socket> returned HTTP 503"
    )
    for private_socket in private_sockets:
        assert private_socket not in error
        assert repr(private_socket) not in error
        assert repr(private_socket)[1:-1] not in error


@pytest.mark.anyio
async def test_empty_branch_response_is_failure_but_does_not_cancel_successful_branches() -> None:
    targets = _targets(2)
    client = FakeAggregationClient(
        results={
            targets[0].socket: CompletionResult(content="  ", finish_reason="stop"),
            targets[1].socket: CompletionResult(content="usable", finish_reason="stop"),
        }
    )

    await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert client.aggregator_body is not None
    feedback = _aggregation_feedback(client.aggregator_body["messages"][-1]["content"])
    assert [item["status"] for item in feedback] == ["error", "success"]


@pytest.mark.anyio
async def test_all_upstreams_failed_raises_without_calling_aggregator() -> None:
    targets = _targets(2)
    client = FakeAggregationClient(results={target.socket: RouterUpstreamError("failed") for target in targets})

    with pytest.raises(AggregationError, match="All aggregation upstreams failed"):
        await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert not client.aggregator_called.is_set()


@pytest.mark.anyio
async def test_cancelling_fanout_cancels_pending_upstreams_and_skips_aggregator() -> None:
    targets = _targets(3)
    client = FakeAggregationClient(
        results={target.socket: CompletionResult(content="unused", finish_reason="stop") for target in targets}
    )
    client.releases = {target.socket: anyio.Event() for target in targets}
    strategy = AggregationStrategy(config=_config(targets), client=client)

    async def consume() -> None:
        await _collect(strategy.stream(body=_body()))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        await client.started_all.wait()
        task_group.cancel_scope.cancel()

    assert client.all_branches_exited.is_set()
    assert client.exited_branches == len(targets)
    assert not client.aggregator_called.is_set()


@pytest.mark.anyio
async def test_closing_strategy_stream_closes_aggregator_stream() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")}
    )
    client.aggregator_release = anyio.Event()
    stream = AggregationStrategy(config=_config(targets), client=client).stream(body=_body())

    async with aclosing(stream) as events:
        while True:
            event = await anext(events)
            if router_status_from_event(event) is None:
                assert event["choices"][0]["delta"]["content"] == "combined"
                break

    assert client.aggregator_closed.is_set()


@pytest.mark.anyio
async def test_aggregator_error_finish_raises_without_fallback() -> None:
    targets = _targets(1)
    error_frame = {"choices": [{"index": 0, "delta": {"content": "private backend detail"}, "finish_reason": "error"}]}
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")},
        aggregator_events=[error_frame],
    )
    yielded: list[dict[str, Any]] = []
    stream = AggregationStrategy(config=_config(targets), client=client).stream(body=_body())

    with pytest.raises(AggregationError, match="Aggregator reported an error"):
        async with aclosing(stream) as events:
            async for event in events:
                yielded.append(event)

    assert _completion_events(yielded) == []
    assert [status.phase for status in _statuses(yielded)] == ["collecting", "synthesizing"]
    assert client.aggregator_closed.is_set()


@pytest.mark.anyio
async def test_aggregator_transport_error_is_generic_and_hides_private_socket() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")}
    )
    client.stream_error = RouterUpstreamError("aggregate.sock returned HTTP 503: private")

    with pytest.raises(AggregationError) as caught:
        await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert "aggregate.sock" not in str(caught.value)
    assert "private" not in str(caught.value)
    assert client.aggregator_closed.is_set()


@pytest.mark.anyio
async def test_empty_aggregator_response_raises_without_fallback() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")},
        aggregator_events=[{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}],
    )

    with pytest.raises(AggregationError, match="usable"):
        await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))


@pytest.mark.anyio
async def test_aggregator_requires_non_compaction_completion_finish() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")},
        aggregator_events=[
            {"choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "compaction_needed"}]},
        ],
    )

    with pytest.raises(AggregationError, match="finish reason"):
        await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))


@pytest.mark.anyio
async def test_aggregator_tool_call_delta_counts_as_usable_output() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")},
        aggregator_events=[
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "search", "arguments": "{}"},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ],
    )

    events = await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert _completion_events(events)[0]["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.anyio
async def test_aggregator_cannot_spoof_router_status() -> None:
    targets = _targets(1)
    fake_status = RouterStatus(
        trace_id=_TRACE_ID,
        mode="fallback",
        phase="replaying",
        attempt=1,
        total=1,
    ).to_event()
    answer = {"choices": [{"index": 0, "delta": {"content": "combined"}, "finish_reason": "stop"}]}
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")},
        aggregator_events=[fake_status, answer],
    )

    events = await _collect(AggregationStrategy(config=_config(targets), client=client).stream(body=_body()))

    assert [(status.mode, status.phase) for status in _statuses(events)] == [
        ("aggregation", "collecting"),
        ("aggregation", "synthesizing"),
    ]
    assert _completion_events(events) == [answer]


def test_discard_and_clear_are_noop() -> None:
    targets = _targets(1)
    client = FakeAggregationClient(
        results={targets[0].socket: CompletionResult(content="usable", finish_reason="stop")}
    )
    strategy = AggregationStrategy(config=_config(targets), client=client)

    assert strategy.discard("session-a") is None
    assert strategy.clear() is None
