"""Search workspace sessions by task category."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def session_task_search(
    category: str,
    workspace: str = "",
    limit: int = 10,
    include_gateway: bool = True,
) -> str:
    """List sessions that match a task category.

    Categories: subagent, github, gateway, background, untitled, recent, all.
    Uses histories, background registry, and optional Gateway titles.

    Args:
        category: Task category name (see above).
        workspace: Workspace root. Empty = current workspace.
        limit: Maximum hits to return (1-50).
        include_gateway: Merge Gateway titles and online session metadata.

    Returns:
        JSON with ok, category, count, hits[] (session_id, title, categories, …).
    """
    result = await _h.task_search_sessions(
        category=category,
        workspace_raw=workspace,
        limit=limit,
        include_gateway=include_gateway,
    )
    return json.dumps(result, ensure_ascii=False)
