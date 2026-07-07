from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from psi_agent.ai.server import _resolve_model, handle_chat_completions


def test_resolve_model_uses_request_override() -> None:
    body = {"model": "gpt-4o-mini", "stream": True, "messages": []}

    model, request_model = _resolve_model(body, "default-model")

    assert model == "gpt-4o-mini"
    assert request_model == "gpt-4o-mini"
    assert "model" not in body
    assert "stream" not in body


def test_resolve_model_falls_back_to_default() -> None:
    body = {"messages": []}

    model, request_model = _resolve_model(body, "default-model")

    assert model == "default-model"
    assert request_model is None


class _FakeChunk:
    def model_dump_json(self) -> str:
        return json.dumps(
            {
                "id": "x",
                "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}],
            }
        )


class _SingleChunkStream:
    def __init__(self) -> None:
        self._yielded = False

    def __aiter__(self) -> _SingleChunkStream:
        return self

    async def __anext__(self) -> _FakeChunk:
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return _FakeChunk()

    async def aclose(self) -> None:
        return None


class _FakeStreamResponse:
    def __init__(self, *, status: int, reason: str, headers: dict[str, str]) -> None:
        self.status = status
        self.reason = reason
        self.headers = headers
        self.writes: list[bytes] = []

    async def prepare(self, request: Any) -> None:
        del request

    async def write(self, data: bytes) -> None:
        self.writes.append(data)


class _FakeRequest:
    def __init__(self, body: dict[str, Any], app: dict[str, Any]) -> None:
        self._body = body
        self.app = app

    async def json(self) -> dict[str, Any]:
        return dict(self._body)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("request_model", "expected_model"),
    [
        ("gpt-4o", "gpt-4o"),
        (None, "default-model"),
    ],
)
async def test_handle_chat_completions_uses_resolved_model(
    monkeypatch: pytest.MonkeyPatch,
    request_model: str | None,
    expected_model: str,
) -> None:
    seen_models: list[str] = []
    created_responses: list[_FakeStreamResponse] = []

    async def fake_acompletion(**kwargs: Any) -> AsyncIterator[_FakeChunk]:
        seen_model = kwargs.get("model")
        if isinstance(seen_model, str):
            seen_models.append(seen_model)
        return _SingleChunkStream()

    def fake_stream_response(*, status: int, reason: str, headers: dict[str, str]) -> _FakeStreamResponse:
        response = _FakeStreamResponse(
            status=status,
            reason=reason,
            headers=headers,
        )
        created_responses.append(response)
        return response

    monkeypatch.setattr("psi_agent.ai.server.acompletion", fake_acompletion)
    monkeypatch.setattr("psi_agent.ai.server.web.StreamResponse", fake_stream_response)

    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    if request_model is not None:
        body["model"] = request_model

    request = _FakeRequest(
        body=body,
        app={
            "provider": "openai",
            "model": "default-model",
            "api_key": "k",
            "base_url": "http://upstream",
        },
    )

    response = await handle_chat_completions(request)

    assert seen_models == [expected_model]
    assert response.status == 200
    assert created_responses
    assert created_responses[0].writes
    assert created_responses[0].writes[0].startswith(b"data: ")
