"""Wait for a subagent AI or Session socket to accept connections."""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _subagent_helpers as _h


async def subagent_wait(socket: str, timeout_seconds: float = 30.0) -> str:
    """Poll until *socket* (TCP, Unix, or Named Pipe) is reachable.

    Args:
        socket: ``ai_socket`` or ``channel_socket`` from ``subagent_plan``.
        timeout_seconds: Max seconds to wait (default 30).

    Returns:
        JSON with ok, socket, message.
    """
    result = await _h.wait_socket(socket, timeout_seconds=timeout_seconds)
    return _h.dumps_result(result)
