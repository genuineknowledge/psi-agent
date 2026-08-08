from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
_mcp_path = TOOLS_DIR / "_fusion_memory_mcp.py"
_mcp_module_name = f"fusion_memory_tool__fusion_memory_mcp_{hashlib.sha256(str(_mcp_path).encode()).hexdigest()[:12]}"
_mcp_module = sys.modules.get(_mcp_module_name)
if _mcp_module is None:
    _mcp_module = types.ModuleType(_mcp_module_name)
    _mcp_module.__file__ = str(_mcp_path)
    sys.modules[_mcp_module_name] = _mcp_module
    exec(compile(_mcp_path.read_text(encoding="utf-8"), str(_mcp_path), "exec"), _mcp_module.__dict__)
CLIENT = _mcp_module.__dict__["CLIENT"]


def dumps_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def parse_json_object(raw: str, field_name: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"{field_name} must be a JSON object string"
    if not isinstance(payload, dict):
        return None, f"{field_name} must be a JSON object"
    return payload, None


def invalid_argument(message: str) -> str:
    return dumps_result(
        {
            "ok": False,
            "error": {"code": "invalid_argument", "message": message, "retryable": False},
        }
    )


def result_object(result: dict[str, Any]) -> dict[str, Any] | None:
    """Unwrap a Fusion Memory payload out of the two envelopes it arrives in.

    ``CLIENT.call_tool`` wraps the MCP transport result, and the Memory tool wraps
    its own ``{"ok", "result"}`` inside that, so a payload sits two levels down:
    ``{"ok": True, "result": {"ok": True, "result": {...}}}``. Peeling only one
    level yields the inner envelope, whose ``state`` is absent — which read as
    ``assignment_state_invalid`` for records that were plainly ``assigned``.

    A payload that is not itself an envelope is returned as-is, so single-envelope
    tools keep working. A failed inner envelope is reported as absent, letting
    callers surface the error instead of treating ``{"ok": False, ...}`` as data.
    """
    payload = result.get("result")
    if not isinstance(payload, dict):
        return None
    if "ok" not in payload and "result" not in payload:
        return payload
    if payload.get("ok") is not True:
        return None
    inner = payload.get("result")
    return inner if isinstance(inner, dict) else None


def bounded_limit(value: int) -> int:
    try:
        return max(1, min(50, int(value)))
    except TypeError:
        return 20
    except ValueError:
        return 20
