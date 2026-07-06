"""Read tool - read file contents."""

from __future__ import annotations

import anyio


async def read(file_path: str, offset: int = 0, limit: int = 0) -> str:
    """Read file contents, optionally with line offset and limit.

    Args:
        file_path: Path to the file to read.
        offset: Line number to start reading from (0-indexed, 0 = beginning).
        limit: Maximum number of lines to read (0 = no limit).

    Returns:
        File contents as a string, or an error message if the file cannot be read.
    """
    path = anyio.Path(file_path)
    if not await path.exists():
        return f"[Error] File not found: {file_path}"
    if not await path.is_file():
        return f"[Error] Not a file: {file_path}"

    content = await path.read_text(encoding="utf-8", errors="replace")

    if offset == 0 and limit == 0:
        return content

    lines = content.splitlines(keepends=True)
    selected = lines[offset:] if limit == 0 else lines[offset : offset + limit]
    return "".join(selected)
