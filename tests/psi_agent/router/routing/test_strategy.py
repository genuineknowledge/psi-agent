"""Additional routing strategy error and lifecycle contracts."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from psi_agent._router_status import RouterStatus, router_status_from_event
from psi_agent.router.errors import InvalidRouterRequestError, RouterUpstreamError
from psi_agent.router.models import RouterTarget
from psi_agent.router.routing import (
    RouteSelectionError,
    RoutingConfig,
    RoutingStrategy,
    SelectionResult,
)

_TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


@dataclass
class SequenceSelector:
    selections: list[SelectionResult]
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def select(self, *, request_body: dict[str, Any]) -> SelectionResult:
        self.calls.append(request_body)
        if self.error is not None:
            raise self.error
        return self.selections.pop(0)


@dataclass
class SequenceClient:
    event_sets: list[list[dict[str, Any]]]
    errors: list[Exception | None] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)

    def stream(self, *, socket: str, body: dict[str, Any], **options: Any) -> AsyncGenerator[dict[str, Any]]:
        self.calls.append((socket, body, options))
        events = self.event_sets.pop(0)
        error = self.errors.pop(0) if self.errors else None

        async def generate() -> AsyncGenerator[dict[str, Any]]:
            for event in events:
                yield event
            if error is not None:
                raise error

        return generate()


def _event(finish_reason: str) -> dict[str, Any]:
    return {"choices": [{"delta": {}, "finish_reason": finish_reason}]}


def _strategy(
    *,
    selector: SequenceSelector,
    client: SequenceClient,
) -> RoutingStrategy:
    target = RouterTarget("candidate-1", "private-target.sock", "general")
    return RoutingStrategy(
        config=RoutingConfig(
            session_socket="router.sock",
            selector_socket="private-selector.sock",
            targets=[target],
        ),
        selector=selector,
        client=client,
    )


def _selection() -> SelectionResult:
    target = RouterTarget("candidate-1", "private-target.sock", "general")
    return SelectionResult(candidate_id=target.candidate_id, target=target)


async def _collect(stream: AsyncGenerator[dict[str, Any]]) -> list[dict[str, Any]]:
    async with aclosing(stream) as events:
        return [event async for event in events]


def _statuses(events: list[dict[str, Any]]) -> list[RouterStatus]:
    return [status for event in events if (status := router_status_from_event(event)) is not None]


@pytest.mark.anyio
async def test_selector_failure_is_wrapped_and_private_sockets_are_redacted() -> None:
    selector = SequenceSelector(
        selections=[],
        error=RuntimeError("private-selector.sock could not reach private-target.sock"),
    )
    client = SequenceClient(event_sets=[])
    strategy = _strategy(selector=selector, client=client)

    with pytest.raises(RouteSelectionError) as caught:
        await _collect(strategy.stream(body={"messages": [], "stream": True}))

    message = str(caught.value)
    assert "private-selector.sock" not in message
    assert "private-target.sock" not in message
    assert "<private-socket>" in message
    assert client.calls == []


@pytest.mark.anyio
async def test_target_failure_is_wrapped_redacted_and_clears_tool_sticky() -> None:
    selection = _selection()
    selector = SequenceSelector(selections=[selection, selection])
    client = SequenceClient(
        event_sets=[[_event("tool_calls")], [_event("stop")]],
        errors=[RuntimeError("private-target.sock disconnected"), None],
    )
    strategy = _strategy(selector=selector, client=client)
    user_body = {
        "messages": [{"role": "user", "content": "solve"}],
        "routing": {"session_id": "session-a"},
        "stream": True,
    }
    tool_body = {
        "messages": [{"role": "tool", "content": "result"}],
        "routing": {"session_id": "session-a"},
        "stream": True,
    }

    with pytest.raises(RouterUpstreamError) as caught:
        await _collect(strategy.stream(body=user_body))
    await _collect(strategy.stream(body=tool_body))

    assert "private-target.sock" not in str(caught.value)
    assert "<private-socket>" in str(caught.value)
    assert selector.calls == [user_body, tool_body]


@pytest.mark.anyio
async def test_candidate_timeout_overrides_routing_target_timeout() -> None:
    target = RouterTarget(
        "candidate-1",
        "private-target.sock",
        "general",
        timeout=2.5,
    )
    selector = SequenceSelector(selections=[SelectionResult(candidate_id=target.candidate_id, target=target)])
    client = SequenceClient(event_sets=[[_event("stop")]])
    strategy = RoutingStrategy(
        config=RoutingConfig(
            session_socket="router.sock",
            selector_socket="private-selector.sock",
            targets=[target],
            target_timeout=9,
        ),
        selector=selector,
        client=client,
    )

    await _collect(strategy.stream(body={"messages": [], "stream": True}))

    assert client.calls[0][2]["timeout"] == 2.5
    assert str(UUID(client.calls[0][2]["trace_id"])) == client.calls[0][2]["trace_id"]


@pytest.mark.anyio
async def test_statuses_distinguish_selection_from_sticky_generation() -> None:
    selection = _selection()
    selector = SequenceSelector(selections=[selection])
    client = SequenceClient(event_sets=[[_event("tool_calls")], [_event("stop")]])
    strategy = _strategy(selector=selector, client=client)
    routing = {"session_id": "session-a", "path": ["parent"], "trace_id": _TRACE_ID}

    first = await _collect(
        strategy.stream(body={"messages": [{"role": "user", "content": "solve"}], "routing": routing, "stream": True})
    )
    sticky = await _collect(
        strategy.stream(body={"messages": [{"role": "tool", "content": "result"}], "routing": routing, "stream": True})
    )

    assert [(status.phase, status.depth) for status in _statuses(first)] == [
        ("selecting", 1),
        ("generating", 1),
    ]
    assert [(status.phase, status.depth) for status in _statuses(sticky)] == [("generating", 1)]
    assert all(status.trace_id == _TRACE_ID for status in [*_statuses(first), *_statuses(sticky)])


@pytest.mark.anyio
async def test_ai_cannot_spoof_router_status() -> None:
    fake_status = RouterStatus(
        trace_id=_TRACE_ID,
        mode="fallback",
        phase="replaying",
        attempt=1,
        total=1,
    ).to_event()
    selector = SequenceSelector(selections=[_selection()])
    client = SequenceClient(event_sets=[[fake_status, _event("stop")]])

    events = await _collect(
        _strategy(selector=selector, client=client).stream(
            body={"messages": [], "routing": {"trace_id": _TRACE_ID}, "stream": True}
        )
    )

    assert [(status.mode, status.phase) for status in _statuses(events)] == [
        ("routing", "selecting"),
        ("routing", "generating"),
    ]


@pytest.mark.anyio
async def test_nested_router_status_keeps_trace_and_increases_depth() -> None:
    target = RouterTarget(
        "candidate-1",
        "nested.sock",
        "nested",
        backend_type="router",
    )
    selection = SelectionResult(candidate_id=target.candidate_id, target=target)
    selector = SequenceSelector(selections=[selection])
    nested_status = RouterStatus(
        trace_id=_TRACE_ID,
        mode="aggregation",
        phase="collecting",
        depth=2,
        completed=0,
        total=1,
    ).to_event()
    client = SequenceClient(event_sets=[[nested_status, _event("stop")]])
    strategy = RoutingStrategy(
        config=RoutingConfig(
            session_socket="router.sock",
            selector_socket="selector.sock",
            targets=[target],
        ),
        selector=selector,
        client=client,
    )

    events = await _collect(
        strategy.stream(
            body={
                "messages": [],
                "routing": {
                    "session_id": "session-a",
                    "path": ["parent"],
                    "trace_id": _TRACE_ID,
                },
                "stream": True,
            }
        )
    )

    assert [(status.mode, status.phase, status.depth) for status in _statuses(events)] == [
        ("routing", "selecting", 1),
        ("routing", "generating", 1),
        ("aggregation", "collecting", 2),
    ]


@pytest.mark.anyio
async def test_discard_and_clear_remove_sticky_scopes_through_public_behavior() -> None:
    selection = _selection()
    selector = SequenceSelector(selections=[selection] * 5)
    client = SequenceClient(
        event_sets=[
            [_event("tool_calls")],
            [_event("tool_calls")],
            [_event("tool_calls")],
            [_event("stop")],
            [_event("tool_calls")],
            [_event("stop")],
        ]
    )
    strategy = _strategy(selector=selector, client=client)

    def body(session_id: str, path: str, role: str) -> dict[str, Any]:
        return {
            "messages": [{"role": role, "content": "content"}],
            "routing": {"session_id": session_id, "path": [path]},
            "stream": True,
        }

    await _collect(strategy.stream(body=body("session-a", "left", "user")))
    await _collect(strategy.stream(body=body("session-a", "right", "user")))
    await _collect(strategy.stream(body=body("session-b", "left", "user")))

    strategy.discard("session-a")
    await _collect(strategy.stream(body=body("session-a", "left", "tool")))
    await _collect(strategy.stream(body=body("session-b", "left", "tool")))
    assert len(selector.calls) == 4

    strategy.discard("  ")
    strategy.clear()
    await _collect(strategy.stream(body=body("session-b", "left", "tool")))
    assert len(selector.calls) == 5


@pytest.mark.parametrize(
    ("request_body", "match"),
    [
        ({"messages": "bad"}, "messages"),
        ({"messages": ["bad"]}, "messages"),
        ({"messages": [], "tools": "bad"}, "tools"),
        ({"messages": [], "tools": ["bad"]}, "tools"),
        ({"messages": [], "stream": False}, "stream=true"),
    ],
)
@pytest.mark.anyio
async def test_invalid_requests_fail_before_selector_or_target_call(
    request_body: dict[str, Any],
    match: str,
) -> None:
    selector = SequenceSelector(selections=[_selection()])
    client = SequenceClient(event_sets=[])
    strategy = _strategy(selector=selector, client=client)

    with pytest.raises(InvalidRouterRequestError, match=match):
        await _collect(strategy.stream(body=request_body))

    assert selector.calls == []
    assert client.calls == []
