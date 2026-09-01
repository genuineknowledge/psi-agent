"""tool_describe — read one tool's full definition, like ``man`` for a tool.

Use this after ``tool_search`` / ``tool_search_code`` narrows you to a
candidate: it returns a single tool's file, human-readable signature, and full
docstring, extracted statically from source (the tool module is never
executed), so you can call the tool correctly on demand.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _tool_index as _idx


async def tool_describe(name: str) -> str:
    """Return the full definition of a single tool by name.

    Discovery tool: given an exact tool name (as shown by ``tool_search``), it
    returns that tool's source file, a human-readable signature with parameter
    types and defaults, and its complete docstring. This lets you read a tool's
    contract on demand instead of keeping every tool definition loaded. The
    definition is parsed statically from the tool's source — the tool is not
    imported or run — so describing a tool is always safe and side-effect free.

    Args:
        name: Exact tool name to describe. If no exact match is found, close
            names (substring matches) are suggested.

    Returns:
        A formatted block with the tool's name, file, signature, and docstring,
        or a not-found message with suggestions.
    """
    metas = await _idx.index_tools()
    by_name = {m.name: m for m in metas}

    meta = by_name.get(name)
    if meta is None:
        needle = name.lower()
        suggestions = sorted(m.name for m in metas if needle in m.name.lower())
        if suggestions:
            hint = ", ".join(suggestions[:10])
            return f"(no tool named {name!r}; did you mean: {hint})"
        return f"(no tool named {name!r}; use tool_search to list available tools)"

    docstring = meta.docstring.strip() or "(no docstring)"
    return f"Tool: {meta.name}\nFile: {meta.file}\nSignature: async def {meta.signature}\n\n{docstring}"
