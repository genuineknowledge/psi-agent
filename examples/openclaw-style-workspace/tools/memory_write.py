"""Write persistent memory to Fusion Memory."""

from __future__ import annotations

from psi_agent.memory.tool_api import memory_write as _memory_write


async def memory_write(content: str, mode: str = "write") -> str:
    """Write a durable fact to persistent memory.

    Args:
        content: Durable user preference, environment fact, or stable project fact.
        mode: Write mode label for audit metadata; usually "write" or "append".

    Returns:
        Save confirmation with basic counts.
    """
    return await _memory_write(content=content, mode=mode)
