"""Read persistent memory from Fusion Memory."""

from __future__ import annotations

from psi_agent.memory.tool_api import memory_read as _memory_read


async def memory_read(query: str = "", limit: int = 8) -> str:
    """Read relevant persistent memory using a query.

    Args:
        query: What to retrieve from persistent memory. Leave empty for broad
            user preferences and stable project facts.
        limit: Maximum number of retrieved memory items to request.

    Returns:
        Retrieved memory context or a no-results message.
    """
    return await _memory_read(query=query, limit=limit)
