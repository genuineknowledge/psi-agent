from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.errors import InvalidRouterRequestError
from psi_agent.router.routing.models import RoutingConfig, RoutingTarget, SelectionResult
from psi_agent.router.routing.strategy import RoutingStrategy


@dataclass
class FakeSelector:
    result: SelectionResult
    received: list[dict[str, Any]] = field(default_factory=list)

    async def select(self, *, request_body: dict[str, Any]) -> SelectionResult:
        self.received.append(request_body)
        return self.result


@dataclass
class FakeStreamingClient:
    events: list[dict[str, Any]]
    calls: list[tuple[str, dict[str, Any], float | None]] = field(default_factory=list)

    def stream(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any]]:
        self.calls.append((socket, body, options.get("timeout")))

        async def generate() -> AsyncGenerator[dict[str, Any]]:
            for event in self.events:
                yield event

        return generate()


def _strategy() -> tuple[RoutingStrategy, FakeSelector, FakeStreamingClient]:
    target = RoutingTarget("code", "code.sock", "coding")
    general = RoutingTarget("general", "general.sock", "general conversation")
    config = RoutingConfig(
        session_socket="router.sock",
        selector_socket="selector.sock",
        targets=[target, general],
        target_timeout=15.0,
    )
    selector = FakeSelector(SelectionResult(candidate_id="code", target=target))
    client = FakeStreamingClient(
        [
            {
                "id": "x",
                "choices": [
                    {"index": 0, "delta": {"content": "hello"}, "finish_reason": None}
                ],
            },
            {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
    )
    return RoutingStrategy(config=config, selector=selector, client=client), selector, client


@pytest.mark.anyio
async def test_strategy_selects_once_and_streams_only_the_selected_target() -> None:
    strategy, selector, client = _strategy()
    body = {
        "model": "private-model-field",
        "messages": [{"role": "user", "content": "write code"}],
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
        "temperature": 0.3,
        "unknown_parameter": {"preserved": True},
        "stream": True,
        "routing": {"session_id": "private-session"},
    }

    events = [event async for event in strategy.stream(body=body)]

    assert len(selector.received) == 1
    assert len(client.calls) == 1
    socket, forwarded, timeout = client.calls[0]
    assert socket == "code.sock"
    assert timeout == 15.0
    assert forwarded == {
        "messages": body["messages"],
        "tools": body["tools"],
        "temperature": 0.3,
        "unknown_parameter": {"preserved": True},
        "stream": True,
    }
    assert events == client.events


@pytest.mark.anyio
async def test_tool_iteration_reuses_sticky_target_for_same_session() -> None:
    strategy, selector, client = _strategy()
    client.events = [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    ]
    first_body = {
        "messages": [{"role": "user", "content": "write and test code"}],
        "stream": True,
        "routing": {"session_id": "session-a"},
    }
    await _consume(strategy=strategy, body=first_body)

    general = strategy.config.targets[1]
    selector.result = SelectionResult(candidate_id="general", target=general)
    continuation_body = {
        "messages": [
            *first_body["messages"],
            {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "file written"},
            {"role": "tool", "tool_call_id": "call-2", "content": "tests passed"},
        ],
        "stream": True,
        "routing": {"session_id": "session-a"},
    }

    await _consume(strategy=strategy, body=continuation_body)

    next_continuation_body = {
        "messages": [
            *continuation_body["messages"],
            {"role": "assistant", "tool_calls": [{"id": "call-3"}]},
            {"role": "tool", "tool_call_id": "call-3", "content": "lint passed"},
        ],
        "stream": True,
        "routing": {"session_id": "session-a"},
    }
    await _consume(strategy=strategy, body=next_continuation_body)

    assert len(selector.received) == 1
    assert [socket for socket, _body, _timeout in client.calls] == [
        "code.sock",
        "code.sock",
        "code.sock",
    ]


@pytest.mark.anyio
async def test_new_user_turn_reclassifies_and_replaces_sticky_target() -> None:
    strategy, selector, client = _strategy()
    client.events = [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    ]
    await _consume(
        strategy=strategy,
        body={
            "messages": [{"role": "user", "content": "write code"}],
            "stream": True,
            "routing": {"session_id": "session-a"},
        },
    )

    general = strategy.config.targets[1]
    selector.result = SelectionResult(candidate_id="general", target=general)
    await _consume(
        strategy=strategy,
        body={
            "messages": [
                {"role": "user", "content": "write code"},
                {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "done"},
                {"role": "user", "content": "now explain the result simply"},
            ],
            "stream": True,
            "routing": {"session_id": "session-a"},
        },
    )

    assert len(selector.received) == 2
    assert [socket for socket, _body, _timeout in client.calls] == [
        "code.sock",
        "general.sock",
    ]


@pytest.mark.anyio
async def test_discard_and_clear_force_next_tool_iteration_to_reclassify() -> None:
    strategy, selector, client = _strategy()
    client.events = [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    ]
    continuation_body = {
        "messages": [
            {"role": "user", "content": "write code"},
            {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "done"},
        ],
        "stream": True,
        "routing": {"session_id": "session-a"},
    }
    await _consume(
        strategy=strategy,
        body={
            "messages": [{"role": "user", "content": "write code"}],
            "stream": True,
            "routing": {"session_id": "session-a"},
        },
    )

    strategy.discard(" session-a ")
    general = strategy.config.targets[1]
    selector.result = SelectionResult(candidate_id="general", target=general)
    await _consume(strategy=strategy, body=continuation_body)

    strategy.clear()
    code = strategy.config.targets[0]
    selector.result = SelectionResult(candidate_id="code", target=code)
    await _consume(strategy=strategy, body=continuation_body)

    assert len(selector.received) == 3
    assert [socket for socket, _body, _timeout in client.calls] == [
        "code.sock",
        "general.sock",
        "code.sock",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": "bad", "stream": True},
        {"messages": [], "tools": "bad", "stream": True},
        {"messages": [], "stream": False},
        {"messages": [], "stream": True, "routing": "bad"},
        {"messages": [], "stream": True, "routing": {"session_id": " "}},
    ],
)
async def test_strategy_rejects_invalid_requests_before_selection(body: dict[str, Any]) -> None:
    strategy, selector, client = _strategy()

    with pytest.raises(InvalidRouterRequestError):
        async for _event in strategy.stream(body=body):
            pass

    assert selector.received == []
    assert client.calls == []


async def _consume(*, strategy: RoutingStrategy, body: dict[str, Any]) -> None:
    async for _event in strategy.stream(body=body):
        pass
