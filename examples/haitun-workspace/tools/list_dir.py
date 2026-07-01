"""List-directory tool - inspect folders and their contents."""

from __future__ import annotations

import anyio


async def list_dir(dir_path: str = ".", recursive: bool = False, max_entries: int = 1000) -> str:
    """List the contents of a directory so folders and files can be recognized.

    Directories are suffixed with ``/`` to distinguish them from files. Use this
    when a path is a folder (``read`` only handles individual files).

    Args:
        dir_path: Path to the directory to list (defaults to the current directory).
        recursive: When True, walk sub-directories and list their contents too.
        max_entries: Maximum number of entries to return (guards against huge trees).

    Returns:
        A newline-separated listing of entries, or an error message if the path
        cannot be listed.
    """
    path = anyio.Path(dir_path)
    if not await path.exists():
        return f"[Error] Path not found: {dir_path}"
    if not await path.is_dir():
        return f"[Error] Not a directory: {dir_path}"

    entries: list[str] = []
    truncated = False

    async def collect(base: anyio.Path, prefix: str) -> None:
        nonlocal truncated
        children = []
        async for child in base.iterdir():
            children.append(child)
        for child in sorted(children, key=lambda c: c.name):
            if len(entries) >= max_entries:
                truncated = True
                return
            is_dir = await child.is_dir()
            display = f"{prefix}{child.name}{'/' if is_dir else ''}"
            entries.append(display)
            if recursive and is_dir:
                await collect(child, f"{prefix}{child.name}/")

    await collect(path, "")

    if not entries:
        return f"(empty directory: {dir_path})"

    listing = "\n".join(entries)
    if truncated:
        listing += f"\n[Truncated at {max_entries} entries]"
    return listing
