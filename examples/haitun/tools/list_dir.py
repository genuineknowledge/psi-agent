"""List-directory tool - inspect folders and their contents."""

from __future__ import annotations

from _background_process_registry import resolve_user_path


async def list_dir(dir_path: str = ".", recursive: bool = False, max_entries: int = 1000) -> str:
    """List the contents of a directory so folders and files can be recognized.

    Relative paths resolve against the **user workspace**. Directories are
    suffixed with ``/``. Use ``find_files`` for recursive glob searches.

    Args:
        dir_path: Path to the directory to list (defaults to the user workspace).
        recursive: When True, walk sub-directories and list their contents too.
        max_entries: Maximum number of entries to return (guards against huge trees).

    Returns:
        A newline-separated listing of entries, or an error message if the path
        cannot be listed.
    """
    path = resolve_user_path(dir_path)
    if not await path.exists():
        return f"[Error] Path not found: {path}"
    if not await path.is_dir():
        return f"[Error] Not a directory: {path}"

    entries: list[str] = []
    truncated = False

    async def collect(base, prefix: str) -> None:
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
        return f"(empty directory: {path})"

    listing = "\n".join(entries)
    if truncated:
        listing += f"\n[Truncated at {max_entries} entries]"
    return listing
