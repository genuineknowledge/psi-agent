"""Spawn or reuse a background subagent Session and run one task."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _subagent_registry as _reg


async def subagent_run(
    task: str,
    workspace: str = "",
    session_id: str = "",
    timeout_seconds: float = _reg.DEFAULT_RUN_TIMEOUT_SECONDS,
) -> str:
    """Delegate a bounded task to a background subagent Session.

    Starts (or reuses) independent ``psi-agent ai`` + ``psi-agent session`` processes
    under the workspace registry — not Gateway. Processes stay alive after the call;
    call ``subagent_stop`` when done, or they are reclaimed after idle timeout
    (``PSI_SUBAGENT_IDLE_SECONDS``, default 1800).

    Args:
        task: Self-contained brief injected as the child Session user message.
        workspace: Executor workspace path. Empty = current workspace.
        session_id: Reuse an existing subagent id for follow-up turns. Empty = new id.
        timeout_seconds: Max seconds to wait for this chat turn (default 600).

    Returns:
        JSON string with ok, status, session_id, text, message, elapsed_seconds, etc.
    """
    result = await _reg.run_subagent(
        task=task,
        workspace_raw=workspace,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    return json.dumps(result, ensure_ascii=False)
