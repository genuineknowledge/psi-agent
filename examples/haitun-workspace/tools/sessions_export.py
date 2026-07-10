"""Export a session transcript to a file (markdown, json, jsonl, or text)."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


async def sessions_export(
    output_path: str,
    session_id: str = "",
    export_format: str = "markdown",
    workspace: str = "",
    include_tool_messages: bool = False,
) -> str:
    """Export session history to a file on disk.

    Primary source is ``histories/{session_id}.jsonl``. Intended for UI-driven
    export (format + path chosen by the user) and agent-side batch export.

    ``markdown`` (default) writes a dialogue-only transcript: user/assistant
    turns with ``### User`` / ``### Assistant`` headings — no session metadata,
    system prompt, tool calls, or reasoning.

    Args:
        output_path: Destination file path. Relative paths resolve under workspace.
        session_id: Target session id. Empty = current session process id.
        export_format: One of ``markdown``, ``json``, ``jsonl``, ``text``.
        workspace: Workspace root. Empty = current workspace.
        include_tool_messages: Include tool-role rows in json/text exports (not markdown).

    Returns:
        JSON with ok, output_path, export_format, bytes_written, message_count, …
    """
    result = await _h.export_session(
        session_id=session_id,
        output_path=output_path,
        export_format=export_format,
        workspace_raw=workspace,
        include_tool_messages=include_tool_messages,
        include_gateway=True,
    )
    return json.dumps(result, ensure_ascii=False)
