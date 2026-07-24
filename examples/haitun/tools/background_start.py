"""Start a detached background shell command."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _background_process_registry as _reg


async def background_start(
    command: str,
    cwd: str = "",
    process_id: str = "",
    workspace: str = "",
    shell: str = "auto",
) -> str:
    """Start a detached background shell command and return its ``process_id``.

    Like ``bash``, *command* is a shell string (Git Bash ``-lc`` on Windows when
    available, else PowerShell). The process keeps running after the tool returns;
    call ``background_stop`` with the returned ``process_id`` when done.

    Args:
        command: Shell command string to run in the background.
        cwd: Working directory. Empty = workspace root.
        process_id: Optional stable id; empty = auto ``bg-…`` id.
        workspace: Registry workspace. Empty = current workspace.
        shell: ``auto`` | ``bash`` | ``powershell``. Subagent on Windows: use ``powershell``.

    Returns:
        JSON with ok, process_id, pid, shell, cwd, message.
    """
    result = await _reg.start_process(
        command=command,
        workspace_raw=workspace,
        cwd=cwd,
        process_id=process_id,
        shell=shell,
    )
    return json.dumps(result, ensure_ascii=False)
