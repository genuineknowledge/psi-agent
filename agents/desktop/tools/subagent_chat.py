"""Send one message to a running subagent Session and verify file deliverables."""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _subagent_helpers as _h


async def subagent_chat(
    channel_socket: str,
    message: str,
    timeout_seconds: float = 600.0,
    workspace: str = "",
    require_files: bool = False,
) -> str:
    """Post *message* to a subagent Session; return final text plus verified files.

    The child must write deliverables into its workspace with the ``write`` tool
    and reply with one ``[SEND:<absolute path>]`` line per file. This tool verifies
    every marker exists on disk; pass ``require_files=true`` when the deliverable is
    files (novels, documents, reports). A reply without existing files is a failure.
    Do NOT rescue content via sessions_export / sessions_history.

    Args:
        channel_socket: From ``subagent_plan`` output.
        message: Self-contained task brief for the child.
        timeout_seconds: Max wait for child reply (default 600).
        workspace: Child workspace root from ``subagent_plan`` output.
        require_files: Treat replies without existing ``[SEND:]`` files as failure.

    Returns:
        JSON with ok, text, files, missing, message, errors.
    """
    result = await _h.chat_subagent(
        channel_socket=channel_socket,
        message=message,
        timeout_seconds=timeout_seconds,
        workspace_raw=workspace,
        require_files=require_files,
    )
    return _h.dumps_result(result)
