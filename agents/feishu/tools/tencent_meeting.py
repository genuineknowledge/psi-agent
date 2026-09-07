"""Tencent Meeting tools exposed to the Feishu Haitun agent.

The upstream skill is distributed as a command-line MCP proxy rather than a
long-lived stdio server.  This thin entry point keeps the agent-facing surface
small while forwarding the JSON-RPC method and parameters unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio

_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "tencent-meeting-mcp" / "scripts" / "tencent_meeting.py"


async def _tencent_meeting_call_with_token_env(
    method: str,
    params_json: str = "",
    *,
    token_env: str = "TENCENT_MEETING_TOKEN",
) -> str:
    """Run the MCP proxy with a selected process environment token.

    ``token_env`` is an internal routing choice made by a fixed meeting task;
    it is never part of the agent-facing tool schema or JSON-RPC payload.
    """

    token = os.environ.get(token_env, "").strip() if token_env else ""
    if not token:
        return f"Error: {token_env} is not configured in the HaiTun process."
    if not _SCRIPT.is_file():
        return f"Error: Tencent Meeting skill entrypoint not found: {_SCRIPT}"

    args = [sys.executable, str(_SCRIPT), method]
    if params_json.strip():
        args.append(params_json)
    child_env = os.environ.copy()
    child_env["TENCENT_MEETING_TOKEN"] = token
    try:
        result = await anyio.run_process(args, check=False, env=child_env)
    except Exception as exc:
        return f"Error: Tencent Meeting tool process failed: {type(exc).__name__}: {exc}"

    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    output = stdout.strip()
    if stderr.strip():
        output = f"{output}\n{stderr.strip()}".strip()
    if result.returncode != 0:
        return f"Error: Tencent Meeting tool exited with code {result.returncode}.\n{output}"
    return output or "(Tencent Meeting tool returned no output)"


async def tencent_meeting_call(method: str, params_json: str = "") -> str:
    """Call a Tencent Meeting MCP method through the installed Tencent skill.

    Use this for meeting lookup, recordings, and transcript retrieval.  For
    transcript analysis, call the recording/transcript methods and prefer the
    original full transcript over smart minutes.  The token is read only from
    the process environment; it is never accepted as a tool argument.

    Args:
        method: JSON-RPC method, usually ``tools/call`` or ``tools/list``.
        params_json: JSON object string passed as the method parameters.  For
            ``tools/call``, include ``name`` and ``arguments``.
    """
    return await _tencent_meeting_call_with_token_env(method, params_json)
