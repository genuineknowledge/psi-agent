"""Inspect runtime and metadata for one workspace session."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def session_status(
    session_id: str = "",
    workspace: str = "",
    include_gateway: bool = True,
) -> str:
    """Inspect one session's runtime info and metadata.

    Merges ``histories/*.jsonl``, background registry, and optional Gateway
    data. Empty ``session_id`` uses the current session process id when available.

    Args:
        session_id: Target session id. Empty = current session process id.
        workspace: Workspace root. Empty = current workspace.
        include_gateway: Merge Gateway online session metadata when reachable.

    Returns:
        JSON with ok, session_id, session{{running, sources, gateway, …}}.
    """
    result = await _h.get_session_status(
        session_id=session_id,
        workspace_raw=workspace,
        include_gateway=include_gateway,
    )
    return json.dumps(result, ensure_ascii=False)
