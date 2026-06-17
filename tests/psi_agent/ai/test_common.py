from __future__ import annotations

import json

from psi_agent.ai.server import ErrorResponse


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
