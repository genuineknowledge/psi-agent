"""tool_search_code — grep the tool source files for a pattern.

Use this when ``tool_search`` (which only looks at names and descriptions)
isn't enough — e.g. to find which tool talks to a given API, imports a given
library, or contains a specific string in its implementation. It searches the
raw source of every tool file and returns ``file:line`` matches, like grep.
"""

from __future__ import annotations

# ruff: noqa: E402
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _tool_index as _idx


async def tool_search_code(pattern: str, limit: int = 50, ignore_case: bool = True) -> str:
    """Search the *source code* of all tool files for a regex/substring.

    Discovery tool that complements ``tool_search``: instead of matching names
    and descriptions, it greps the actual implementation of every tool file.
    Use it to locate a tool by an API name, library import, env var, or any
    literal that appears in code but not in the docstring. Results are
    ``file:line: matched line`` entries. Note this searches whole tool files
    (including private ``_``-prefixed helper files), not just public tools.

    Args:
        pattern: A regular expression to search for. If it is not valid regex,
            it falls back to a literal substring search.
        limit: Maximum number of matching lines to return (default 50).
        ignore_case: Case-insensitive matching when True (default True).

    Returns:
        A newline-separated list of ``file:line: text`` matches, or a message
        when nothing matches.
    """
    flags = re.IGNORECASE if ignore_case else 0
    fallback_note = ""
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        regex = re.compile(re.escape(pattern), flags)
        fallback_note = f"[Invalid regex ({exc}); searched for literal text instead]\n"

    directory = _idx.tools_dir()
    hits: list[str] = []
    truncated = False

    try:
        is_dir = await directory.is_dir()
    except OSError:
        is_dir = False
    if not is_dir:
        return "(tools directory is not accessible)"

    paths = [p async for p in directory.glob("*.py")]
    for path in sorted(paths, key=lambda p: p.name):
        try:
            source = await path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        for lineno, line in enumerate(source.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{path.name}:{lineno}: {line.strip()}")
                if len(hits) >= limit:
                    truncated = True
                    break
        if truncated:
            break

    if not hits:
        return f"{fallback_note}(no tool source matches {pattern!r})"

    out = fallback_note + "\n".join(hits)
    if truncated:
        out += f"\n[Truncated at {limit} matches — narrow the pattern for more]"
    return out
