"""Edit file content with exact string replacement."""

from __future__ import annotations

import anyio


async def tool(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Edit a file by replacing exact string matches.

    Args:
        file_path: Path to the file to edit.
        old_string: The exact string to find and replace.
        new_string: The string to replace old_string with.
        replace_all: If True, replace all occurrences. If False (default),
            only replace if there's exactly one match.

    Returns:
        Success message or error message if edit fails.
    """
    try:
        # Read the file
        async with await anyio.open_file(file_path, encoding="utf-8") as f:
            content = await f.read()

        # Count matches
        count = content.count(old_string)

        if count == 0:
            return f"Error: String not found in {file_path}"

        if count > 1 and not replace_all:
            return (
                f"Error: Found {count} occurrences of the string in {file_path}. "
                "Use replace_all=True to replace all occurrences."
            )

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        # Write back
        async with await anyio.open_file(file_path, mode="w", encoding="utf-8") as f:
            await f.write(new_content)

        occurrences = "all occurrences" if replace_all else "1 occurrence"
        return f"Successfully replaced {occurrences} in {file_path}"

    except FileNotFoundError:
        return f"Error: File not found: {file_path}"
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except OSError as e:
        return f"Error: Could not edit file {file_path}: {e}"
