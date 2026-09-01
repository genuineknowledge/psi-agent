from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

_mcp_path = Path(__file__).resolve().parent / "_haibao_mcp.py"
_mcp_source = _mcp_path.read_bytes()
_mcp_prefix = f"haibao_tool__haibao_mcp_{hashlib.sha256(str(_mcp_path).encode()).hexdigest()[:12]}_"
_mcp_name = f"{_mcp_prefix}{hashlib.sha256(_mcp_source).hexdigest()[:12]}"
for _stale_name in tuple(sys.modules):
    if _stale_name.startswith(_mcp_prefix) and _stale_name != _mcp_name:
        sys.modules.pop(_stale_name)
_mcp_module: Any = sys.modules.get(_mcp_name)
if _mcp_module is None:
    _mcp_module = types.ModuleType(_mcp_name)
    _mcp_module.__file__ = str(_mcp_path)
    sys.modules[_mcp_name] = _mcp_module
    try:
        exec(compile(_mcp_source, str(_mcp_path), "exec"), _mcp_module.__dict__)
    except BaseException:
        if sys.modules.get(_mcp_name) is _mcp_module:
            sys.modules.pop(_mcp_name)
        raise


async def haibao_list_datasets() -> str:
    """List Haibao datasets authorized for the current MCP principal."""
    result = await _mcp_module.call_tool("haibao_list_datasets", {})
    return json.dumps(result, ensure_ascii=False)
