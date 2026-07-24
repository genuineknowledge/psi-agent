"""Search workspace session histories by keyword."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def session_keyword_search(
    query: str,
    session_id: str = "",
    workspace: str = "",
    limit: int = 10,
) -> str:
    """Search session histories for a keyword or phrase.

    Scans ``histories/*.jsonl`` (Gateway-optional). Pass ``session_id`` to search
    inside one session only.

    Args:
        query: Case-insensitive substring to match in user/assistant messages.
        session_id: Optional scope. Empty = search all sessions in the workspace.
        workspace: Workspace root. Empty = current workspace.
        limit: Maximum number of session hits to return (1-50).

    Returns:
        JSON with ok, query, count, hits[] (session_id, snippets, score, …).
    """
    result = await _h.keyword_search_sessions(
        query=query,
        session_id=session_id,
        workspace_raw=workspace,
        limit=limit,
    )
    return json.dumps(result, ensure_ascii=False)
