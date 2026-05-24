"""Shared utilities for AI backends."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class ErrorResponse:
    message: str
    type: str
    code: str

    def to_dict(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def build_error_sse_chunk(content: str) -> str:
    return json.dumps(
        {
            "id": "error",
            "model": "",
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "error"}],
        }
    )
