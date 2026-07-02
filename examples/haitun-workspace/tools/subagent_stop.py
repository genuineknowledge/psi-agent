"""Stop background subagent Sessions and list active ones."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _subagent_registry as _reg


async def subagent_stop(session_id: str, workspace: str = "") -> str:
    """Stop a background subagent and release its processes.

    Prefer calling this when the subagent job is fully delivered and you will not
    send follow-ups on the same ``session_id``. Also runs idle reclamation first.

    Args:
        session_id: Subagent id previously returned by ``subagent_run``.
        workspace: Registry workspace. Empty = current workspace.

    Returns:
        JSON string with ok, session_id, message, and idle_reclaimed ids if any.
    """
    ws = _reg.resolve_workspace(workspace)
    reclaimed = await _reg.sweep_idle_sessions(ws)
    stopped = await _reg.stop_session(ws, session_id.strip())
    if stopped:
        payload = {
            "ok": True,
            "session_id": session_id,
            "message": "stopped",
            "idle_reclaimed": reclaimed,
        }
    else:
        payload = {
            "ok": False,
            "session_id": session_id,
            "message": f"session not found or already stopped: {session_id!r}",
            "idle_reclaimed": reclaimed,
        }
    return json.dumps(payload, ensure_ascii=False)


async def subagent_list(workspace: str = "") -> str:
    """List active background subagents for the workspace registry.

    Args:
        workspace: Registry workspace. Empty = current workspace.

    Returns:
        JSON string with sessions list and idle_limit_seconds.
    """
    ws = _reg.resolve_workspace(workspace)
    await _reg.sweep_idle_sessions(ws)
    sessions = await _reg.list_sessions(ws)
    payload = {
        "ok": True,
        "workspace": str(ws),
        "idle_limit_seconds": _reg.idle_seconds_from_env(),
        "sessions": sessions,
    }
    return json.dumps(payload, ensure_ascii=False)
