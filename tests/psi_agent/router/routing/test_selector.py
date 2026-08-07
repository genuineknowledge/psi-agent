"""LLM-backed route selector contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.router.errors import InvalidRouterRequestError
from psi_agent.router.models import CompletionResult, RouterTarget
from psi_agent.router.routing import RouteSelectionError, RouteSelector, RoutingConfig


@dataclass
class FakeCompletionClient:
    result: CompletionResult
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> CompletionResult:
        self.calls.append((socket, body, options))
        return self.result


def _targets() -> list[RouterTarget]:
    return [
        RouterTarget("code", "private-code.sock", "programming"),
        RouterTarget("math", "private-math.sock", "mathematics"),
    ]


def _selector(
    result: CompletionResult,
    *,
    max_chars: int = 12_000,
    request_overrides: dict[str, Any] | None = None,
) -> tuple[RouteSelector, FakeCompletionClient]:
    client = FakeCompletionClient(result)
    config = RoutingConfig(
        session_socket="router.sock",
        selector_socket="private-selector.sock",
        targets=_targets(),
        selector_timeout=7,
        max_selection_chars=max_chars,
        selector_request_overrides=request_overrides or {},
    )
    return RouteSelector(config=config, client=client), client


@pytest.mark.anyio
async def test_select_maps_strict_candidate_id_without_exposing_private_sockets() -> None:
    selector, client = _selector(CompletionResult(content=' {"candidate_id":"math"} ', finish_reason="stop"))
    request = {
        "messages": [{"role": "user", "content": "2 + 2"}],
        "tools": [{"type": "function", "function": {"name": "calculator", "description": "calculate"}}],
        "routing": {"session_id": "private-session"},
        "model": "private-model",
    }

    selection = await selector.select(request_body=request)

    assert selection.candidate_id == "math"
    assert selection.target == _targets()[1]
    assert len(client.calls) == 1
    socket, body, options = client.calls[0]
    assert socket == "private-selector.sock"
    assert options == {"timeout": 7}
    assert body["temperature"] == 0
    serialized = json.dumps(body, ensure_ascii=False)
    assert "private-code.sock" not in serialized
    assert "private-math.sock" not in serialized
    assert "private-session" not in serialized
    assert "private-model" not in serialized


@pytest.mark.anyio
async def test_selector_request_overrides_apply_to_the_actual_control_request() -> None:
    overrides: dict[str, Any] = {
        "temperature": 0.6,
        "max_tokens": 32,
        "provider_option": {"nested": ["original"]},
    }
    selector, client = _selector(
        CompletionResult(content='{"candidate_id":"code"}', finish_reason="stop"),
        request_overrides=overrides,
    )
    overrides["provider_option"]["nested"].append("mutated")

    await selector.select(request_body={"messages": [{"role": "user", "content": "solve"}]})

    _, body, _ = client.calls[0]
    assert body["temperature"] == 0.6
    assert body["max_tokens"] == 32
    assert body["provider_option"] == {"nested": ["original"]}
    assert body["stream"] is True
    assert body["messages"][0]["role"] == "system"


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (CompletionResult(content="not-json", finish_reason="stop"), "not valid JSON"),
        (CompletionResult(content="[]", finish_reason="stop"), "only candidate_id"),
        (
            CompletionResult(content='{"candidate_id":"math","reason":"best"}', finish_reason="stop"),
            "only candidate_id",
        ),
        (CompletionResult(content='{"candidate_id":"unknown"}', finish_reason="stop"), "unknown candidate"),
        (CompletionResult(content='{"candidate_id":1}', finish_reason="stop"), "unknown candidate"),
        (CompletionResult(content='{"candidate_id":"math"}', finish_reason="length"), "finish reason"),
        (
            CompletionResult(
                content='{"candidate_id":"math"}',
                tool_calls=[{"id": "call"}],
                finish_reason="stop",
            ),
            "tool calls",
        ),
    ],
)
@pytest.mark.anyio
async def test_select_rejects_non_strict_or_incomplete_decisions(result: CompletionResult, match: str) -> None:
    selector, _ = _selector(result)

    with pytest.raises(RouteSelectionError, match=match):
        await selector.select(request_body={"messages": [{"role": "user", "content": "solve"}]})


def test_build_request_keeps_recent_context_and_summarizes_multimodal_and_tools() -> None:
    selector, _ = _selector(CompletionResult(), max_chars=180)
    long_early_message = "old-private-context-" * 30
    long_description = "d" * 300
    request = {
        "messages": [
            {"role": "user", "content": long_early_message},
            {"role": "assistant", "content": [{"type": "image_url"}, {"type": "text"}]},
            {"role": "user", "content": "latest task"},
            {"content": "missing role"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "search", "description": long_description}},
            {"type": "function", "function": {"name": "calculator", "description": 42}},
            {"type": "function", "function": {"description": "missing name"}},
            {"type": "function", "function": "invalid"},
        ],
    }
    original = deepcopy(request)

    body = selector.build_request(request_body=request)

    payload = json.loads(body["messages"][1]["content"])
    assert payload["conversation"][-1] == {"role": "user", "content": "latest task"}
    assert long_early_message not in body["messages"][1]["content"]
    assert any("multimodal content with 2 block(s)" in item["content"] for item in payload["conversation"])
    assert payload["available_tools"] == [
        {"name": "search", "description": "d" * 256},
        {"name": "calculator", "description": ""},
    ]
    assert request == original


def test_build_request_keeps_tail_of_one_oversized_latest_message() -> None:
    selector, _ = _selector(CompletionResult(), max_chars=100)
    content = "discard-this-prefix-" + "z" * 200

    body = selector.build_request(request_body={"messages": [{"role": "user", "content": content}]})

    payload = json.loads(body["messages"][1]["content"])
    compacted = payload["conversation"]
    assert len(compacted) == 1
    assert compacted[0]["role"] == "user"
    assert compacted[0]["content"]
    assert content.endswith(compacted[0]["content"])
    assert "discard-this-prefix" not in compacted[0]["content"]


@pytest.mark.parametrize(
    ("request_body", "match"),
    [
        ({"messages": "bad"}, "messages"),
        ({"messages": ["bad"]}, "messages"),
        ({"messages": [], "tools": "bad"}, "tools"),
        ({"messages": [], "tools": ["bad"]}, "tools"),
    ],
)
def test_build_request_rejects_invalid_public_shapes(request_body: dict[str, Any], match: str) -> None:
    selector, _ = _selector(CompletionResult())

    with pytest.raises(InvalidRouterRequestError, match=match):
        selector.build_request(request_body=request_body)
