"""Stop background OS processes and list active ones."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _background_process_registry as _reg


async def background_stop(process_id: str, workspace: str = "") -> str:
    """Stop a background process registered by ``background_start``.

    Args:
        process_id: Id returned from ``background_start``.
        workspace: Registry workspace. Empty = current workspace.

    Returns:
        JSON with ok, process_id, pid, message.
    """
    result = await _reg.stop_process(process_id=process_id, workspace_raw=workspace)
    return json.dumps(result, ensure_ascii=False)


async def background_list(workspace: str = "") -> str:
    """List registered background processes for the workspace.

    Args:
        workspace: Registry workspace. Empty = current workspace.

    Returns:
        JSON with ok, processes list (process_id, pid, alive, command, …).
    """
    result = await _reg.list_processes(workspace_raw=workspace)
    return json.dumps(result, ensure_ascii=False)
