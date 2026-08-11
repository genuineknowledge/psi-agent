"""Behavior contracts for strictly serial fallback routing."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from typing import Any

import anyio
import pytest
from anyio.lowlevel import checkpoint

from psi_agent._router_status import RouterStatus, router_status_from_event
from psi_agent.router.errors import InvalidRouterRequestError, RouterUpstreamError
from psi_agent.router.fallback import FallbackConfig, FallbackError, FallbackStrategy
from psi_agent.router.models import BufferedCompletion, CompletionResult, RouterTarget

_TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


def _event(
    *,
    content: str = "",
    reasoning: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
    event_id: str = "event",
) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {
        "id": event_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _completion(
    *,
    content: str = "answer",
    reasoning: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    events: tuple[dict[str, Any], ...] | None = None,
) -> BufferedCompletion:
    calls = tool_calls or []
    buffered_events = events or (
        _event(content=content, reasoning=reasoning, tool_calls=calls or None, finish_reason=finish_reason),
    )
    return BufferedCompletion(
        events=buffered_events,
        completion=CompletionResult(
            content=content,
            reasoning=reasoning,
            tool_calls=calls,
            finish_reason=finish_reason,
        ),
    )


def _tool_completion(*, content: str = "") -> BufferedCompletion:
    calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
    ]
    return _completion(content=content, tool_calls=calls, finish_reason="tool_calls")


def _targets() -> list[RouterTarget]:
    return [
        RouterTarget("candidate-1", "private-1.sock", "primary"),
        RouterTarget("candidate-2", "private-2.sock", "secondary", backend_type="router"),
        RouterTarget("candidate-3", "private-3.sock", "last resort"),
    ]


def _body(*, role: str = "user", path: list[str] | None = None) -> dict[str, Any]:
    return {
        "messages": [{"role": role, "content": "solve"}],
        "tools": [],
        "stream": True,
        "model": "private-model",
        "temperature": 0.2,
        "routing": {"session_id": "session-a", "path": path or [], "trace_id": _TRACE_ID},
    }


class FakeBufferedClient:
    def __init__(self, outcomes: dict[str, list[BufferedCompletion | Exception]]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.active = 0
        self.max_active = 0

    async def buffered_complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> BufferedCompletion:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append((socket, deepcopy(body), options))
        try:
            await checkpoint()
            outcome = self.outcomes[socket].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            self.active -= 1


async def _collect(stream: AsyncGenerator[dict[str, Any]]) -> list[dict[str, Any]]:
    async with aclosing(stream) as events:
        return [event async for event in events]


def _statuses(events: list[dict[str, Any]]) -> list[RouterStatus]:
    return [status for event in events if (status := router_status_from_event(event)) is not None]


def _completion_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if router_status_from_event(event) is None]


def _strategy(client: Any) -> FallbackStrategy:
    return FallbackStrategy(
        config=FallbackConfig(
            session_socket="fallback.sock",
            targets=_targets(),
            target_timeout=7,
        ),
        client=client,
    )


@pytest.mark.anyio
async def test_first_success_stops_without_calling_later_candidates() -> None:
    first = _completion(events=(_event(content="one", event_id="a"), _event(finish_reason="stop", event_id="b")))
    client = FakeBufferedClient({"private-1.sock": [first]})

    events = await _collect(_strategy(client).stream(body=_body()))

    assert _completion_events(events) == list(first.events)
    assert [(status.phase, status.attempt, status.total) for status in _statuses(events)] == [
        ("attempting", 1, 3),
        ("replaying", 1, 3),
    ]
    assert [call[0] for call in client.calls] == ["private-1.sock"]
    assert client.calls[0][2] == {"timeout": 7, "trace_id": _TRACE_ID}


@pytest.mark.anyio
async def test_failure_events_are_discarded_and_attempts_are_strictly_serial() -> None:
    failed = _completion(
        content="",
        reasoning="private thought",
        events=(_event(content="must-not-leak", finish_reason="stop"),),
    )
    success = _completion(
        content="winner",
        events=(
            _event(content="win", event_id="success-1"),
            _event(content="ner", finish_reason="stop", event_id="success-2"),
        ),
    )
    client = FakeBufferedClient(
        {
            "private-1.sock": [failed],
            "private-2.sock": [RouterUpstreamError("temporary")],
            "private-3.sock": [success],
        }
    )

    events = await _collect(_strategy(client).stream(body=_body()))

    assert _completion_events(events) == list(success.events)
    assert "must-not-leak" not in str(events)
    assert [(status.phase, status.attempt) for status in _statuses(events)] == [
        ("attempting", 1),
        ("switching", 2),
        ("attempting", 2),
        ("switching", 3),
        ("attempting", 3),
        ("replaying", 3),
    ]
    assert all(status.trace_id == _TRACE_ID for status in _statuses(events))
    assert [call[0] for call in client.calls] == [
        "private-1.sock",
        "private-2.sock",
        "private-3.sock",
    ]
    assert client.max_active == 1
    assert "routing" not in client.calls[0][1]
    assert client.calls[1][1]["routing"] == {
        "session_id": "session-a",
        "path": ["candidate-2"],
        "trace_id": _TRACE_ID,
    }


@pytest.mark.anyio
async def test_candidate_timeout_overrides_fallback_target_timeout_per_attempt() -> None:
    targets = [
        RouterTarget("candidate-1", "private-1.sock", "primary", timeout=2.5),
        RouterTarget("candidate-2", "private-2.sock", "secondary"),
    ]
    client = FakeBufferedClient(
        {
            "private-1.sock": [RouterUpstreamError("temporary")],
            "private-2.sock": [_completion(content="winner")],
        }
    )
    strategy = FallbackStrategy(
        config=FallbackConfig(
            session_socket="fallback.sock",
            targets=targets,
            target_timeout=7,
        ),
        client=client,
    )

    await _collect(strategy.stream(body=_body()))

    assert [(socket, options) for socket, _, options in client.calls] == [
        ("private-1.sock", {"timeout": 2.5, "trace_id": _TRACE_ID}),
        ("private-2.sock", {"timeout": 7, "trace_id": _TRACE_ID}),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        _completion(content="", reasoning="thinking", finish_reason="stop"),
        _completion(content="text", finish_reason="error"),
        _completion(content="text", finish_reason=""),
        _completion(content="text", finish_reason="compaction_needed"),
    ],
)
async def test_unusable_completions_fall_through(result: BufferedCompletion) -> None:
    client = FakeBufferedClient(
        {
            "private-1.sock": [result],
            "private-2.sock": [_completion(content="usable")],
        }
    )

    events = await _collect(_strategy(client).stream(body=_body()))

    assert _completion_events(events)[0]["choices"][0]["delta"]["content"] == "usable"
    assert [call[0] for call in client.calls] == ["private-1.sock", "private-2.sock"]


@pytest.mark.anyio
async def test_all_failures_are_ordered_bounded_and_hide_private_sockets() -> None:
    long_error = "private-1.sock " + "x" * 800
    client = FakeBufferedClient(
        {
            "private-1.sock": [RouterUpstreamError(long_error)],
            "private-2.sock": [TimeoutError("private-2.sock timed out")],
            "private-3.sock": [RouterUpstreamError("private-3.sock failed")],
        }
    )

    with pytest.raises(FallbackError) as caught:
        await _collect(_strategy(client).stream(body=_body()))

    message = str(caught.value)
    assert message.index("candidate-1") < message.index("candidate-2") < message.index("candidate-3")
    assert "<private-socket>" in message
    assert all(target.socket not in message for target in _targets())
    assert "x" * 513 not in message


@pytest.mark.anyio
async def test_tool_sticky_falls_forward_updates_and_clears_without_wraparound() -> None:
    client = FakeBufferedClient(
        {
            "private-1.sock": [
                _tool_completion(),
                RouterUpstreamError("primary failed during tool loop"),
                _completion(content="new run"),
            ],
            "private-2.sock": [_tool_completion(), _completion(content="finished")],
        }
    )
    strategy = _strategy(client)

    await _collect(strategy.stream(body=_body()))
    await _collect(strategy.stream(body=_body(role="tool")))
    await _collect(strategy.stream(body=_body(role="tool")))
    await _collect(strategy.stream(body=_body(role="tool")))

    assert [call[0] for call in client.calls] == [
        "private-1.sock",
        "private-1.sock",
        "private-2.sock",
        "private-2.sock",
        "private-1.sock",
    ]


@pytest.mark.anyio
async def test_sticky_scopes_are_isolated_by_path_and_discarded_by_session() -> None:
    client = FakeBufferedClient(
        {
            "private-1.sock": [_tool_completion(), _tool_completion()],
        }
    )
    strategy = _strategy(client)

    await _collect(strategy.stream(body=_body(path=["left"])))
    await _collect(strategy.stream(body=_body(path=["right"])))

    assert set(strategy._sticky_targets) == {
        ("session-a", ("left",)),
        ("session-a", ("right",)),
    }
    strategy.discard("session-a")
    assert strategy._sticky_targets == {}


@pytest.mark.anyio
async def test_cancellation_stops_current_attempt_without_trying_next() -> None:
    class BlockingClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.started = anyio.Event()
            self.closed = anyio.Event()

        async def buffered_complete(
            self,
            *,
            socket: str,
            body: dict[str, Any],
            **options: Any,
        ) -> BufferedCompletion:
            del body, options
            self.calls.append(socket)
            self.started.set()
            try:
                await anyio.sleep_forever()
                raise AssertionError("sleep_forever returned unexpectedly")
            finally:
                self.closed.set()

    client = BlockingClient()
    strategy = _strategy(client)

    async def consume() -> None:
        await _collect(strategy.stream(body=_body()))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        await client.started.wait()
        task_group.cancel_scope.cancel()

    assert client.calls == ["private-1.sock"]
    assert client.closed.is_set()
    assert strategy._sticky_targets == {}


@pytest.mark.anyio
async def test_closing_replay_does_not_retry_and_clears_sticky_state() -> None:
    success = _tool_completion()
    success = BufferedCompletion(
        events=(_event(content="first"), _event(tool_calls=[], finish_reason="tool_calls")),
        completion=success.completion,
    )
    client = FakeBufferedClient({"private-1.sock": [success]})
    strategy = _strategy(client)
    stream = strategy.stream(body=_body())

    async with aclosing(stream) as events:
        while True:
            event = await anext(events)
            if router_status_from_event(event) is None:
                assert event["choices"][0]["delta"]["content"] == "first"
                break

    assert [call[0] for call in client.calls] == ["private-1.sock"]
    assert strategy._sticky_targets == {}


@pytest.mark.anyio
async def test_replay_drops_stale_nested_router_status_frames() -> None:
    nested_status = RouterStatus(
        trace_id=_TRACE_ID,
        mode="routing",
        phase="generating",
        depth=1,
    ).to_event()
    success = _completion(
        content="winner",
        events=(nested_status, _event(content="winner", finish_reason="stop")),
    )
    client = FakeBufferedClient({"private-1.sock": [success]})

    events = await _collect(_strategy(client).stream(body=_body()))

    assert [(status.mode, status.phase, status.depth) for status in _statuses(events)] == [
        ("fallback", "attempting", 0),
        ("fallback", "replaying", 0),
    ]
    assert _completion_events(events) == [_event(content="winner", finish_reason="stop")]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": "bad"},
        {"messages": [], "tools": "bad"},
        {"messages": [], "tools": [], "stream": False},
    ],
)
@pytest.mark.anyio
async def test_invalid_requests_fail_before_an_upstream_call(body: dict[str, Any]) -> None:
    client = FakeBufferedClient({})

    with pytest.raises(InvalidRouterRequestError):
        await _collect(_strategy(client).stream(body=body))

    assert client.calls == []
