"""Send one message to a running subagent Session (no bash, no reasoning dump)."""

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
) -> str:
    """Post *message* to a subagent Session and return final text only.

    Reasoning chunks are not included in the returned text.

    Args:
        channel_socket: From ``subagent_plan`` output.
        message: Self-contained task brief for the child.
        timeout_seconds: Max wait for child reply (default 600).

    Returns:
        JSON with ok, text, message.
    """
    result = await _h.chat_subagent(
        channel_socket=channel_socket,
        message=message,
        timeout_seconds=timeout_seconds,
    )
    return _h.dumps_result(result)
