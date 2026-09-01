"""Hand off task + context from one session to another (work transfer)."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def sessions_handoff(
    target_session_id: str,
    task: str,
    source_session_id: str = "",
    query: str = "",
    category: str = "",
    context: str = "",
    workspace: str = "",
    wait: bool = False,
    timeout_seconds: float = 600.0,
    history_limit: int = 20,
) -> str:
    """Transfer work context from a source session into a target session.

    Builds a structured handoff message (task + context) and posts it to the
    target session channel. Use with ``session_keyword_search`` /
    ``session_task_search`` to locate the source session by phrase or category.

    Args:
        target_session_id: Session that should continue the work (must be running).
        task: What the target session should do next.
        source_session_id: Session to copy context from. Empty = current session,
            or auto-pick the top ``query`` / ``category`` search hit when set.
        query: Optional keyword to filter context and/or find source session.
        category: Optional ``session_task_search`` category to find source session
            when ``source_session_id`` is empty (e.g. ``github``, ``recent``).
        context: Manual context override; skips history extraction when non-empty.
        workspace: Workspace root. Empty = current workspace.
        wait: If true, wait for the target session reply text.
        timeout_seconds: Max wait for target reply when ``wait`` is true.
        history_limit: Max source messages to scan when building context.

    Returns:
        JSON with ok, source_session_id, target_session_id, context_body, reply_text, …
    """
    result = await _h.handoff_session(
        target_session_id=target_session_id,
        task=task,
        source_session_id=source_session_id,
        query=query,
        category=category,
        context=context,
        workspace_raw=workspace,
        wait=wait,
        timeout_seconds=timeout_seconds,
        history_limit=history_limit,
        include_gateway=True,
    )
    return json.dumps(result, ensure_ascii=False)
