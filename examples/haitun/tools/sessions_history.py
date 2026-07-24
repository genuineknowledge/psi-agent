"""Read message history for one workspace session."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def sessions_history(
    session_id: str = "",
    workspace: str = "",
    limit: int = 50,
    include_tool_messages: bool = False,
    include_gateway: bool = True,
) -> str:
    """Read conversation history for one session.

    Primary source is ``histories/{session_id}.jsonl``. When the file is missing
    and Gateway is online, falls back to ``GET /sessions/{id}/history``.

    Args:
        session_id: Target session id. Empty = current session process id.
        workspace: Workspace root. Empty = current workspace.
        limit: Maximum number of messages to return (from the end). Max 500.
        include_tool_messages: Include tool-role rows and assistant tool_calls.
        include_gateway: Allow Gateway history fallback when jsonl is absent.

    Returns:
        JSON with ok, session_id, messages[], count, history_source, running, …
    """
    result = await _h.get_session_history(
        session_id=session_id,
        workspace_raw=workspace,
        limit=limit,
        include_tool_messages=include_tool_messages,
        include_gateway=include_gateway,
    )
    return json.dumps(result, ensure_ascii=False)
