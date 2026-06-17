"""Glob tool — expand glob patterns and return sorted matching paths."""

from __future__ import annotations

import anyio


async def glob_files(pattern: str, path: str = ".") -> str:
    """Expand a glob pattern and return matching file/directory paths.

    Args:
        pattern: Glob pattern (e.g. "tools/*.py", "skills/*/SKILL.md").
        path: Base directory for the glob. Defaults to current directory.

    Returns:
        Sorted newline-separated list of matching paths, or a no-match message.
    """
    root = anyio.Path(path)
    matches: list[str] = []

    async for entry in root.glob(pattern):
        matches.append(str(entry))

    if not matches:
        return f"No paths found matching: {pattern}"

    matches.sort()
    return "\n".join(matches)
