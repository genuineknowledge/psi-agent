"""Plan subagent spawn: paths, TCP sockets (Windows), and ready-to-run commands."""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _subagent_helpers as _h


async def subagent_plan(session_id: str = "", workspace: str = "") -> str:
    """Return subagent spawn plan (sockets, process ids, shell commands).

    Does **not** start processes — call ``background_start`` with returned
    ``session_command`` (and ``ai_command`` only when ``reuse_parent_ai`` is false).
    Under Gateway, reuses the linked parent AI socket (no API key in env).
    On Windows the child **channel** uses TCP.

    Args:
        session_id: Reuse id for follow-up, or empty for new ``sub-…`` id.
        workspace: Executor workspace. Empty = current workspace.

    Returns:
        JSON with ok, session_id, ai_socket, channel_socket, ai_command,
        session_command, shell, ai_process_id, session_process_id, repo_root, …
    """
    result = await _h.plan_subagent(session_id=session_id, workspace_raw=workspace)
    return _h.dumps_result(result)
