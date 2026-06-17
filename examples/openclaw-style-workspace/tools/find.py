"""Find tool — locate files by glob pattern."""

from __future__ import annotations

import anyio


async def find(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern under a directory.

    Args:
        pattern: Glob pattern to match (e.g. "**/*.py", "*.md").
        path: Root directory to search in. Defaults to current directory.

    Returns:
        Newline-separated list of matching file paths, or a no-match message.
    """
    root = anyio.Path(path)
    matches: list[str] = []

    use_rglob = "**" in pattern or "/" not in pattern
    iterator = root.rglob(pattern) if use_rglob else root.glob(pattern)
    async for entry in iterator:
        matches.append(str(entry))

    if not matches:
        return f"No files found matching: {pattern}"

    matches.sort()
    return "\n".join(matches)
