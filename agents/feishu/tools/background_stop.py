"""Stop background OS processes and list active ones."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _background_process_registry as _reg


async def background_stop(process_id: str, workspace: str = "") -> str:
    """Stop a background process registered by ``background_start``.

    Args:
        process_id: Id returned from ``background_start``.
        workspace: Registry workspace. Empty = current workspace.

    Returns:
        JSON with ok, process_id, pid, message.
    """
    result = await _reg.stop_process(process_id=process_id, workspace_raw=workspace)
    return json.dumps(result, ensure_ascii=False)


async def background_list(workspace: str = "") -> str:
    """List registered background processes for the workspace.

    Args:
        workspace: Registry workspace. Empty = current workspace.

    Returns:
        JSON with ok, processes list (process_id, pid, alive, command, log_path, …).
    """
    result = await _reg.list_processes(workspace_raw=workspace)
    return json.dumps(result, ensure_ascii=False)


async def background_output(
    process_id: str,
    workspace: str = "",
    tail_lines: int = 200,
    max_chars: int = 20000,
) -> str:
    """Read the output a background process has produced so far.

    Use this to run work that would blow past the ``bash`` timeout: start it
    with ``background_start``, then poll here. Long API paging loops belong on
    this path — a foreground ``bash`` call that exceeds its limit is killed and
    tells you nothing about how far it got.

    Output is a snapshot while ``alive`` is true; call again to see more. It
    stays readable after the process exits, so this also answers "what did it
    finish with?".

    Args:
        process_id: Id returned from ``background_start``.
        workspace: Registry workspace. Empty = current workspace.
        tail_lines: Return only the last N lines (0 = all). The end is where a
            long run stopped, which is the part worth reading.
        max_chars: Cap on returned characters, applied after ``tail_lines``.

    Returns:
        JSON with ok, alive, output, total_lines, omitted_leading_lines, log_path.
    """
    result = await _reg.read_output(
        process_id=process_id,
        workspace_raw=workspace,
        tail_lines=tail_lines,
        max_chars=max_chars,
    )
    return json.dumps(result, ensure_ascii=False)
