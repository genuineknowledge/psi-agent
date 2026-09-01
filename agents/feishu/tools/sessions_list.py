"""List workspace sessions from histories, background registry, and optional Gateway."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def sessions_list(
    workspace: str = "",
    running_only: bool = False,
    include_gateway: bool = True,
) -> str:
    """List sessions known to this workspace.

    Primary sources (Gateway-optional): ``histories/*.jsonl`` and the background
    process registry. When Gateway is reachable, online sessions and titles are
    merged in.

    Args:
        workspace: Workspace root. Empty = current workspace.
        running_only: When true, return only sessions with a live background or Gateway runtime.
        include_gateway: When true, merge Gateway ``/sessions`` and ``/titles`` if online.

    Returns:
        JSON with ok, workspace, gateway_url, current_session_id, count, sessions[].
    """
    result = await _h.list_sessions(
        workspace_raw=workspace,
        running_only=running_only,
        include_gateway=include_gateway,
    )
    return json.dumps(result, ensure_ascii=False)
