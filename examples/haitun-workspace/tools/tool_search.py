"""tool_search — find tools by keyword, like ``apropos`` for the tool catalog.

Use this to discover which tools exist without needing every tool definition
loaded up front. It matches your keywords against each tool's name and
description and returns a compact list (name + one-line summary), so you can
then call ``tool_describe`` to read the full definition of the one you want.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _tool_index as _idx


async def tool_search(query: str, limit: int = 20) -> str:
    """Search the tool catalog by keyword (name + description).

    Discovery tool: given free-text keywords, it ranks tools by how many of
    those keywords appear in the tool's name and description, and returns a
    compact list of ``name — summary (file)`` lines. Use it to find candidate
    tools in a large catalog, then call ``tool_describe`` to read a tool's full
    signature and docstring before invoking it. Pair with ``tool_search_code``
    when you need to match on implementation detail rather than description.

    Args:
        query: Space-separated keywords. Matching is case-insensitive; a tool
            ranks higher the more distinct keywords it matches. An empty query
            lists all tools.
        limit: Maximum number of tools to return (default 20).

    Returns:
        A newline-separated list of ``name — summary (file)`` lines, or a
        message when nothing matches.
    """
    metas = await _idx.index_tools()
    terms = [t.lower() for t in query.split() if t.strip()]

    if not terms:
        ranked = metas
    else:
        scored: list[tuple[int, _idx.ToolMeta]] = []
        for meta in metas:
            haystack = f"{meta.name}\n{meta.description}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, meta))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        ranked = [meta for _, meta in scored]

    if not ranked:
        return f"(no tools match {query!r})"

    truncated = len(ranked) > limit
    ranked = ranked[:limit]
    lines = [f"{m.name} — {m.summary or '(no description)'} ({m.file})" for m in ranked]
    out = "\n".join(lines)
    if truncated:
        out += f"\n[Truncated at {limit} results — narrow the query for more]"
    return out
