from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.models import CompletionResult
from psi_agent.router.routing.errors import RouteSelectionError
from psi_agent.router.routing.models import RoutingConfig, RoutingTarget
from psi_agent.router.routing.selector import RouteSelector


@dataclass
class FakeCompletionClient:
    result: CompletionResult
    calls: list[tuple[str, dict[str, Any], float | None]] = field(default_factory=list)

    async def complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CompletionResult:
        self.calls.append((socket, body, options.get("timeout")))
        return self.result


def _config() -> RoutingConfig:
    return RoutingConfig(
        session_socket="router.sock",
        selector_socket="selector-private.sock",
        targets=[
            RoutingTarget("cheap-chat", "cheap-private.sock", "low-cost conversation"),
            RoutingTarget("strong-code", "code-private.sock", "complex coding and debugging"),
        ],
        selector_timeout=9.0,
    )


def _body() -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": "agent context"},
            {"role": "user", "content": "debug this Python program"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read a local file",
                    "parameters": {},
                },
            }
        ],
        "stream": True,
    }


def test_selector_request_exposes_ids_and_descriptions_but_not_sockets() -> None:
    client = FakeCompletionClient(CompletionResult())
    request = RouteSelector(config=_config(), client=client).build_request(request_body=_body())
    serialized = json.dumps(request, ensure_ascii=False)

    assert "cheap-chat" in serialized
    assert "strong-code" in serialized
    assert "complex coding and debugging" in serialized
    assert "cheap-private.sock" not in serialized
    assert "code-private.sock" not in serialized
    assert "read_file" in serialized
    assert request["temperature"] == 0


@pytest.mark.anyio
async def test_selector_maps_one_opaque_id_to_private_target() -> None:
    client = FakeCompletionClient(
        CompletionResult(content='{"candidate_id":"strong-code"}', finish_reason="stop")
    )
    selector = RouteSelector(config=_config(), client=client)

    result = await selector.select(request_body=_body())

    assert result.candidate_id == "strong-code"
    assert result.target.socket == "code-private.sock"
    assert client.calls[0][0] == "selector-private.sock"
    assert client.calls[0][2] == 9.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"candidate_id":"unknown"}',
        '{"candidate_id":"strong-code","socket":"code-private.sock"}',
        '["strong-code"]',
    ],
)
async def test_selector_rejects_malformed_or_unconfigured_decisions(content: str) -> None:
    client = FakeCompletionClient(CompletionResult(content=content, finish_reason="stop"))

    with pytest.raises(RouteSelectionError):
        await RouteSelector(config=_config(), client=client).select(request_body=_body())
