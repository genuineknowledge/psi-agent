"""Edit tool — make precise string replacements in files."""

from __future__ import annotations

import anyio


async def edit(file_path: str, old_string: str, new_string: str) -> str:
    """Make a precise string replacement in a file.

    The old_string must appear exactly once in the file. If it appears
    zero or more than once, the edit is rejected to prevent ambiguous changes.

    Args:
        file_path: Path to the file to edit.
        old_string: The exact string to find and replace.
        new_string: The string to replace it with.

    Returns:
        Success message or error message describing what went wrong.
    """
    path = anyio.Path(file_path)
    if not await path.exists():
        return f"[Error] File not found: {file_path}"

    content = await path.read_text(encoding="utf-8", errors="replace")
    count = content.count(old_string)

    if count == 0:
        return "[Error] old_string not found in file"
    if count > 1:
        return f"[Error] old_string appears {count} times; must be unique to edit safely"

    new_content = content.replace(old_string, new_string, 1)
    await path.write_text(new_content, encoding="utf-8")
    return f"[OK] Replaced 1 occurrence in {file_path}"
