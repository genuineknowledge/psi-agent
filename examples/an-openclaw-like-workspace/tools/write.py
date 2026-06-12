"""Write file content asynchronously."""

from __future__ import annotations

import anyio


async def tool(file_path: str, content: str) -> str:
    """Write content to a file asynchronously.

    Creates the file if it doesn't exist, overwrites if it does.

    Args:
        file_path: Path to the file to write.
        content: Content to write to the file.

    Returns:
        Success message or error message if write fails.
    """
    try:
        async with await anyio.open_file(file_path, mode="w", encoding="utf-8") as f:
            await f.write(content)
        return f"Successfully wrote {len(content)} characters to {file_path}"
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except OSError as e:
        return f"Error: Could not write to file {file_path}: {e}"
