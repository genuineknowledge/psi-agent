from __future__ import annotations

import json

from psi_agent.ai.common import ErrorResponse, build_error_sse_chunk


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


def test_build_error_sse_chunk() -> None:
    chunk = build_error_sse_chunk("[Upstream Error 401]: Unauthorized")
    data = json.loads(chunk)
    assert data["choices"][0]["delta"]["content"] == "[Upstream Error 401]: Unauthorized"
    assert data["choices"][0]["finish_reason"] == "error"
