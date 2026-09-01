from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Literal

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


async def memory_search(
    query: str,
    limit: int = 8,
    visibility: Literal["personal", "organization"] = "personal",
) -> str:
    """Search raw evidence in exactly one scope: personal or organization."""
    try:
        bounded_limit = max(1, min(32, int(limit)))
    except TypeError, ValueError:
        return json.dumps(
            {
                "ok": False,
                "error": {"code": "invalid_argument", "message": "limit must be an integer", "retryable": False},
            },
            ensure_ascii=False,
        )
    normalized_visibility = visibility.strip().lower() if isinstance(visibility, str) else ""
    if normalized_visibility not in {"personal", "organization"}:
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "invalid_argument",
                    "message": "visibility must be personal or organization",
                    "retryable": False,
                },
            },
            ensure_ascii=False,
        )
    caller = CLIENT.call_organization_read_tool if normalized_visibility == "organization" else CLIENT.call_tool
    result = await caller(
        "memory_search",
        {"query": query, "limit": bounded_limit, "visibility": normalized_visibility},
        retryable=True,
    )
    return json.dumps(result, ensure_ascii=False)
