from __future__ import annotations

import json

import pytest
from aiohttp.client_exceptions import ClientConnectionResetError

from psi_agent.ai.common import ErrorResponse, SSEChunk, write_sse_bytes


def test_error_response_to_dict() -> None:
    err = ErrorResponse(message="msg", type="err", code="500")
    d = err.to_dict()
    assert d["error"]["message"] == "msg"
    assert d["error"]["type"] == "err"
    assert d["error"]["code"] == "500"


def test_error_response_to_json() -> None:
    err = ErrorResponse(message="Something wrong", type="internal_error", code="500")
    data = json.loads(err.to_json())
    assert data["error"]["message"] == "Something wrong"
    assert data["error"]["code"] == "500"


def test_sse_chunk_content() -> None:
    chunk = SSEChunk(delta_content="Hello", finish_reason="stop", chunk_id="c1")
    sse = chunk.to_sse()
    assert sse.startswith("data: ")
    data = json.loads(sse[6:].strip())
    assert data["choices"][0]["delta"]["content"] == "Hello"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["id"] == "c1"


def test_sse_chunk_reasoning() -> None:
    chunk = SSEChunk(delta_reasoning="Let me think...")
    sse = chunk.to_sse()
    data = json.loads(sse[6:].strip())
    assert data["choices"][0]["delta"]["reasoning_content"] == "Let me think..."
    assert data["choices"][0]["finish_reason"] is None


def test_sse_chunk_error() -> None:
    chunk = SSEChunk(
        delta_content="[Upstream Error 401]: Unauthorized",
        finish_reason="error",
        chunk_id="error",
    )
    sse = chunk.to_sse()
    data = json.loads(sse[6:].strip())
    assert data["choices"][0]["delta"]["content"] == "[Upstream Error 401]: Unauthorized"
    assert data["choices"][0]["finish_reason"] == "error"
    assert data["id"] == "error"


def test_sse_chunk_tool_calls() -> None:
    chunk = SSEChunk(
        delta_tool_calls=[
            {"index": 0, "id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"cmd":"ls"}'}},
        ],
        chunk_id="chatcmpl-5",
    )
    sse = chunk.to_sse()
    data = json.loads(sse[6:].strip())
    tc = data["choices"][0]["delta"]["tool_calls"]
    assert len(tc) == 1
    assert tc[0]["function"]["name"] == "bash"


def test_sse_chunk_empty_to_sse() -> None:
    chunk = SSEChunk()
    sse = chunk.to_sse()
    assert sse.startswith("data: ")
    data = json.loads(sse[6:].strip())
    assert data["choices"][0]["delta"] == {}
    assert data["choices"][0]["finish_reason"] is None
    assert data["id"] == "chatcmpl-unknown"


@pytest.mark.anyio
async def test_write_sse_bytes_returns_false_when_client_disconnects() -> None:
    class ClosedResponse:
        async def write(self, _payload: bytes) -> None:
            raise ClientConnectionResetError("Cannot write to closing transport")

    assert await write_sse_bytes(ClosedResponse(), b"data: {}\n\n") is False
