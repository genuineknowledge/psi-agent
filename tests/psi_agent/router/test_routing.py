from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.client import UpstreamResult
from psi_agent.router.protocol import RouterConfig
from psi_agent.router.routing import RoutingOrchestrator


@dataclass
class FakeClient:
    results: list[UpstreamResult]
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult:
        self.calls.append((socket, body, options))
        return self.results.pop(0)


def config() -> RouterConfig:
    return RouterConfig(
        session_socket="session",
        router_socket="router",
        default_socket="default",
        mode="routing",
        upstream=[("code", "coding"), ("chat", "conversation")],
    )


def body() -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": "system context"},
            {"role": "user", "content": "please implement"},
        ],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "routing": {"session_id": "private"},
        "model": "private-model",
    }


@pytest.mark.anyio
async def test_successful_routing_selection_forwards_full_context_to_selected_socket() -> None:
    client = FakeClient(
        [
            UpstreamResult(content='{"socket": "code"}', finish_reason="stop"),
            UpstreamResult(content="done", reasoning="why", finish_reason="stop"),
        ]
    )

    result = await RoutingOrchestrator(config=config(), client=client).process(body=body())

    assert result == UpstreamResult(content="done", reasoning="why", finish_reason="stop")
    assert [socket for socket, _, _ in client.calls] == ["router", "code"]
    route_body = client.calls[0][1]
    assert route_body["messages"][:-1] == body()["messages"]
    assert "tools" not in route_body
    upstream_body = client.calls[1][1]
    assert upstream_body == {
        "messages": body()["messages"],
        "tools": body()["tools"],
        "temperature": 0.2,
        "stream": True,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selection",
    [
        "not json",
        '{"socket": "unknown"}',
        '{"socket": 42}',
        '{"socket": "code", "extra": true}',
        '["code"]',
    ],
)
async def test_malformed_or_unknown_routing_selection_falls_back_to_default_socket(selection: str) -> None:
    client = FakeClient(
        [
            UpstreamResult(content=selection, finish_reason="stop"),
            UpstreamResult(content="fallback", finish_reason="stop"),
        ]
    )

    result = await RoutingOrchestrator(config=config(), client=client).process(body=body())

    assert result.content == "fallback"
    assert [socket for socket, _, _ in client.calls] == ["router", "default"]
