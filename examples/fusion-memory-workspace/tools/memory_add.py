from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

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


async def memory_add(content: str, source: str = "fusion-memory-tool") -> str:
    """Store durable information in Fusion Memory."""
    result = await CLIENT.call_tool(
        "memory_add",
        {
            "content": content,
            "source": source or "fusion-memory-tool",
        },
        retryable=False,
    )
    return json.dumps(result, ensure_ascii=False)
