"""Create a new session via Gateway (POST /sessions)."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def sessions_create(
    workspace: str = "",
    session_id: str = "",
    ai_id: str = "",
) -> str:
    """Create a new Gateway-managed session runtime.

    Requires Gateway online with at least one linked AI. Does not hand off
    context — pair with ``sessions_handoff`` in ``session-management`` skill.

    Args:
        workspace: Workspace directory for the new session. Empty = current workspace.
        session_id: Optional fixed id. Empty = Gateway generates one.
        ai_id: Optional AI backend id. Empty = infer from Gateway (single AI or
            an existing session in the same workspace).

    Returns:
        JSON with ok, session_id, channel_socket, ai_id, workspace, gateway_url, …
    """
    result = await _h.create_session(
        workspace_raw=workspace,
        session_id=session_id,
        ai_id=ai_id,
        include_gateway=True,
    )
    return json.dumps(result, ensure_ascii=False)
