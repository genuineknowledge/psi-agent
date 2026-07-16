"""Tools whose return value must appear in the same user-visible reply.

Some workspace tools (e.g. ``clarify``) only *build* question text. The model
is supposed to paste it into ``delta.content``, but often leaves it in the tool
/ ``reasoning`` channel. Channels that hide ``reasoning`` then show a bubble
that *promises* options without rendering them.

Session fixes that for every Channel by:
1. streaming the tool text as ``content`` (same turn / same bubble),
2. merging it into the tool_calls assistant's ``content`` for history,
3. ending the turn (no extra model round that talks past the question).
"""

from __future__ import annotations

# Tool names (workspace ``tools/*.py`` async function names) whose successful
# return value is meant for the user verbatim.
USER_VISIBLE_RESULT_TOOLS: frozenset[str] = frozenset({"clarify"})


def surface_tool_result_text(func_name: str, result: object) -> str | None:
    """Return text to stream/persist as visible content, or ``None`` to skip."""
    if func_name not in USER_VISIBLE_RESULT_TOOLS:
        return None
    text = str(result)
    if not text.strip() or text.startswith("[Error]"):
        return None
    return text
