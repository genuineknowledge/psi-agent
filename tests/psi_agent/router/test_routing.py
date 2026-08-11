from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.models import RouterTarget
from psi_agent.router.routing import RoutingConfig, RoutingStrategy, SelectionResult


@dataclass
class FakeSelector:
    selections: list[SelectionResult]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def select(self, *, request_body: dict[str, Any]) -> SelectionResult:
        self.requests.append(request_body)
        return self.selections.pop(0)


@dataclass
class FakeClient:
    event_sets: list[list[dict[str, Any]]]
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)

    def stream(self, *, socket: str, body: dict[str, Any], **options: Any) -> AsyncGenerator[dict[str, Any]]:
        self.calls.append((socket, body, options))
        events = self.event_sets.pop(0)

        async def _events() -> AsyncGenerator[dict[str, Any]]:
            for event in events:
                yield event

        return _events()


def _config(target: RouterTarget) -> RoutingConfig:
    return RoutingConfig(
        session_socket="session",
        selector_socket="selector",
        targets=[target],
        target_timeout=12.0,
    )


async def _collect(stream: AsyncGenerator[dict[str, Any]]) -> list[dict[str, Any]]:
    async with aclosing(stream) as events:
        return [event async for event in events]


@pytest.mark.anyio
async def test_routing_forwards_only_public_parameters_to_selected_target() -> None:
    target = RouterTarget(candidate_id="code", socket="code-socket", description="Coding")
    selector = FakeSelector([SelectionResult(candidate_id="code", target=target)])
    client = FakeClient([[{"choices": [{"delta": {}, "finish_reason": "stop"}]}]])
    strategy = RoutingStrategy(config=_config(target), selector=selector, client=client)
    source = {
        "messages": [{"role": "user", "content": "implement"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "future_parameter": {"enabled": True},
        "model": "private-model",
        "routing": {"session_id": "private"},
        "stream": True,
    }

    await _collect(strategy.stream(body=source))

    assert selector.requests == [source]
    assert client.calls == [
        (
            "code-socket",
            {
                "messages": [{"role": "user", "content": "implement"}],
                "tools": [{"type": "function", "function": {"name": "search"}}],
                "temperature": 0.2,
                "future_parameter": {"enabled": True},
                "stream": True,
            },
            {"timeout": 12.0, "trace_id": source["routing"]["trace_id"]},
        )
    ]
    client.calls[0][1]["messages"][0]["content"] = "changed"
    assert source["messages"][0]["content"] == "implement"


@pytest.mark.anyio
async def test_routing_reuses_sticky_target_for_one_tool_iteration() -> None:
    target = RouterTarget(candidate_id="code", socket="code-socket", description="Coding")
    selection = SelectionResult(candidate_id="code", target=target)
    selector = FakeSelector([selection, selection])
    client = FakeClient(
        [
            [{"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}],
            [{"choices": [{"delta": {}, "finish_reason": "stop"}]}],
            [{"choices": [{"delta": {}, "finish_reason": "stop"}]}],
        ]
    )
    strategy = RoutingStrategy(config=_config(target), selector=selector, client=client)
    first_body = {
        "messages": [{"role": "user", "content": "implement"}],
        "routing": {"session_id": "session-a"},
        "stream": True,
    }
    tool_body = {
        "messages": [{"role": "tool", "content": "tool result"}],
        "routing": {"session_id": "session-a"},
        "stream": True,
    }
    next_body = {
        "messages": [{"role": "user", "content": "another task"}],
        "routing": {"session_id": "session-a"},
        "stream": True,
    }

    await _collect(strategy.stream(body=first_body))
    await _collect(strategy.stream(body=tool_body))
    await _collect(strategy.stream(body=next_body))

    assert selector.requests == [first_body, next_body]
    assert [socket for socket, _, _ in client.calls] == ["code-socket"] * 3


@pytest.mark.anyio
async def test_routing_sticky_is_isolated_by_composition_path() -> None:
    target = RouterTarget(
        candidate_id="nested",
        socket="nested-socket",
        description="Nested Router",
        backend_type="router",
    )
    selection = SelectionResult(candidate_id="nested", target=target)
    selector = FakeSelector([selection, selection])
    client = FakeClient(
        [
            [{"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}],
            [{"choices": [{"delta": {}, "finish_reason": "stop"}]}],
            [{"choices": [{"delta": {}, "finish_reason": "stop"}]}],
        ]
    )
    strategy = RoutingStrategy(config=_config(target), selector=selector, client=client)
    left_user = {
        "messages": [{"role": "user", "content": "left"}],
        "routing": {"session_id": "session-a", "path": ["left"]},
        "stream": True,
    }
    right_tool = {
        "messages": [{"role": "tool", "content": "right result"}],
        "routing": {"session_id": "session-a", "path": ["right"]},
        "stream": True,
    }
    left_tool = {
        "messages": [{"role": "tool", "content": "left result"}],
        "routing": {"session_id": "session-a", "path": ["left"]},
        "stream": True,
    }

    await _collect(strategy.stream(body=left_user))
    await _collect(strategy.stream(body=right_tool))
    await _collect(strategy.stream(body=left_tool))

    assert selector.requests == [left_user, right_tool]
    assert [body["routing"]["path"] for _, body, _ in client.calls] == [
        ["left", "nested"],
        ["right", "nested"],
        ["left", "nested"],
    ]
