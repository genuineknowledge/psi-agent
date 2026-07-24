"""Session search tool — search past conversation transcripts."""

from __future__ import annotations


async def tool(query: str) -> str:
    """Search past session transcripts for relevant context.

    Args:
        query: The search query string to look for in past sessions.

    Returns:
        Matching excerpts from past sessions, or a not-implemented message.
    """
    return (
        "session_search is not implemented in this workspace. "
        f"Could not search for: {query!r}. "
        "To enable cross-session search, implement a transcript store and "
        "replace this stub with real search logic."
    )
