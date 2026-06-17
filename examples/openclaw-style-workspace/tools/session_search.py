"""Session search tool — search workspace text files for a query string."""

from __future__ import annotations

import os

import anyio


_TEXT_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".sh", ".env", ".cfg", ".ini", ".log",
}
_MAX_RESULTS = 20
_CONTEXT_LINES = 2


async def session_search(query: str, path: str = "") -> str:
    """Search workspace text files for a query string.

    Args:
        query: Text or substring to search for (case-insensitive).
        path: Directory to search. Defaults to PSI_WORKSPACE_DIR or ".".

    Returns:
        Matching snippets with file path and surrounding context lines.
    """
    if not query.strip():
        return "[Error] 'query' must not be empty."

    search_root = anyio.Path(path or os.environ.get("PSI_WORKSPACE_DIR", "."))
    query_lower = query.lower()
    results: list[str] = []

    async for file_path in search_root.rglob("*"):
        if len(results) >= _MAX_RESULTS:
            break
        if not await file_path.is_file():
            continue
        if file_path.suffix not in _TEXT_EXTENSIONS:
            continue
        # Skip hidden dirs and __pycache__
        parts = file_path.parts
        if any(p.startswith(".") or p == "__pycache__" for p in parts):
            continue

        try:
            content = await file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines):
            if query_lower in line.lower():
                start = max(0, i - _CONTEXT_LINES)
                end = min(len(lines), i + _CONTEXT_LINES + 1)
                snippet_lines = lines[start:end]
                snippet = "\n".join(
                    f"{'>' if j == i - start else ' '} {lines[start + j]}"
                    for j in range(len(snippet_lines))
                )
                results.append(f"**{file_path}:{i + 1}**\n{snippet}")
                break  # One match per file

    if not results:
        return f"No results found for: {query}"

    return "\n\n".join(results)
