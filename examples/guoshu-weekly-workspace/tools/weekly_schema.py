from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

_MCP_PATH = Path(__file__).resolve().parent / "_weekly_mcp.py"
_MODULE_NAME = f"guoshu_weekly_tool__weekly_mcp_{hashlib.sha256(str(_MCP_PATH).encode()).hexdigest()[:12]}"
_module = sys.modules.get(_MODULE_NAME)
if _module is None:
    _module = types.ModuleType(_MODULE_NAME)
    _module.__file__ = str(_MCP_PATH)
    sys.modules[_MODULE_NAME] = _module
    exec(compile(_MCP_PATH.read_text(encoding="utf-8"), str(_MCP_PATH), "exec"), _module.__dict__)
_call = _module.__dict__["call"]


async def weekly_schema(board: str = "") -> str:
    """List weekly-report boards, category trees, and the field dictionary.

    Call this first when a question's board or category is ambiguous, or when you
    need to know which fields exist before claiming something is unanswerable.
    The returned field_notes carry the calibers that constrain every answer.

    Args:
        board: Optional board code (tech/group) or name to scope the category tree.
    """
    return await _call("weekly_schema", {"board": board})
