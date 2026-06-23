"""Persistent memory tool backed by Fusion Memory HTTP."""

from __future__ import annotations

from psi_agent.memory.tool_api import memory_action


async def memory(
    action: str = "read",
    content: str = "",
    query: str = "",
    section: str = "",
) -> str:
    """Read, search, write, append, or clear persistent Fusion Memory.

    Args:
        action: One of "read", "search", "write", "append", or "clear".
        content: Text to store for write/append, or a fallback query for read/search.
        query: Retrieval query for read/search actions.
        section: Optional section label to prefix stored content or guide retrieval.

    Returns:
        Retrieved memory context, save confirmation, or clear confirmation.
    """
    return await memory_action(action=action, content=content, query=query, section=section)
