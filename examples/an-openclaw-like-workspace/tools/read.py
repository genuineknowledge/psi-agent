"""Read file content asynchronously."""

from __future__ import annotations

import anyio


async def tool(file_path: str) -> str:
    """Read file content asynchronously.

    Args:
        file_path: Path to the file to read.

    Returns:
        File content as string, or error message if file cannot be read.
    """
    try:
        async with await anyio.open_file(file_path, encoding="utf-8") as f:
            return await f.read()
    except FileNotFoundError:
        return f"Error: File not found: {file_path}"
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except OSError as e:
        return f"Error: Could not read file {file_path}: {e}"
