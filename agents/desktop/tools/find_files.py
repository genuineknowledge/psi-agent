"""Find-files tool - recursively locate files by glob pattern."""

from __future__ import annotations

from pathlib import Path

import _runtime_paths as _paths


async def find_files(
    pattern: str = "**/*",
    dir_path: str = ".",
    max_results: int = 1000,
) -> str:
    """Recursively find file paths matching a glob pattern, newest first.

    Complements ``list_dir`` (single level, no pattern): use this when you
    need to locate files across nested directories by name or extension,
    e.g. ``**/*.py`` for every Python file in the tree. Results are sorted
    by modification time (most recently modified first) so the files you are
    likely working on surface at the top. Only files are returned; matching
    directories are skipped. Mirrors Python's ``pathlib.Path.rglob``.

    Relative ``dir_path`` (including default ``.``) resolves under the current
    Session workspace (``get_workspace()``). Absolute paths are used as-is.

    Args:
        pattern: Glob pattern to match, relative to ``dir_path``. ``**``
            matches any number of directories, so ``**/*.py`` finds all
            ``.py`` files recursively. A plain ``*.py`` matches the top
            level only. Defaults to ``**/*`` (every file in the tree).
        dir_path: Directory to search from (defaults to the workspace root).
        max_results: Maximum number of paths to return (guards against huge
            trees).

    Returns:
        A newline-separated list of matching file paths sorted by
        modification time (newest first), or an error message if the search
        root cannot be used.
    """
    base = _paths.resolve_user_path(dir_path)
    if not await base.exists():
        return f"[Error] Path not found: {base}"
    if not await base.is_dir():
        return f"[Error] Not a directory: {base}"

    matches: list[tuple[float, str]] = []
    truncated = False

    async for match in base.glob(pattern):
        if not await match.is_file():
            continue
        try:
            stat = await match.stat()
            mtime = stat.st_mtime
        except OSError:
            # File vanished or is inaccessible between glob and stat; skip it.
            continue
        matches.append((mtime, str(Path(match))))

    matches.sort(key=lambda item: item[0], reverse=True)

    if len(matches) > max_results:
        matches = matches[:max_results]
        truncated = True

    if not matches:
        return f"(no files match {pattern!r} under {base})"

    listing = "\n".join(path for _, path in matches)
    if truncated:
        listing += f"\n[Truncated at {max_results} results]"
    return listing
