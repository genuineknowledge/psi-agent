"""Write tool — create or overwrite files."""

from __future__ import annotations

import anyio


async def write(file_path: str, content: str) -> str:
    """Create or overwrite a file with the given content.

    Args:
        file_path: Path to the file to write.
        content: Content to write to the file.

    Returns:
        Success message or error message.
    """
    path = anyio.Path(file_path)
    parent = path.parent
    if not await parent.exists():
        await parent.mkdir(parents=True, exist_ok=True)

    await path.write_text(content, encoding="utf-8")
    size = len(content.encode("utf-8"))
    return f"[OK] Written {size} bytes to {file_path}"
