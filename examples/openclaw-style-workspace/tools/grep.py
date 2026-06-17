"""Grep tool — search file contents using regex patterns."""

from __future__ import annotations

import asyncio


async def grep(
    pattern: str,
    path: str = ".",
    glob: str = "",
    case_insensitive: bool = False,
) -> str:
    """Search file contents for a regex pattern.

    Uses ripgrep (rg) if available, falls back to grep.

    Args:
        pattern: Regular expression pattern to search for.
        path: Directory or file path to search in.
        glob: Optional glob pattern to filter files (e.g. "*.py").
        case_insensitive: If True, perform case-insensitive search.

    Returns:
        Matching lines with file paths and line numbers, or a no-match message.
    """
    # Try ripgrep first
    rg_args = ["rg", "--line-number", "--with-filename"]
    if case_insensitive:
        rg_args.append("--ignore-case")
    if glob:
        rg_args.extend(["--glob", glob])
    rg_args.extend([pattern, path])

    try:
        process = await asyncio.create_subprocess_exec(
            *rg_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode == 0:
            return stdout.decode(errors="replace").rstrip() or "(no output)"
        if process.returncode == 1:
            return f"No matches found for pattern: {pattern}"
        # rg not found or error — fall through to grep
        if b"No such file or directory" in stderr or b"not found" in stderr:
            raise FileNotFoundError
        return stderr.decode(errors="replace") or f"No matches found for pattern: {pattern}"
    except (FileNotFoundError, asyncio.TimeoutError):
        pass

    # Fallback: grep
    grep_args = ["grep", "--line-number", "--with-filename", "--recursive"]
    if case_insensitive:
        grep_args.append("--ignore-case")
    if glob:
        grep_args.extend(["--include", glob])
    grep_args.extend([pattern, path])

    process = await asyncio.create_subprocess_exec(
        *grep_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    if process.returncode == 0:
        return stdout.decode(errors="replace").rstrip() or "(no output)"
    if process.returncode == 1:
        return f"No matches found for pattern: {pattern}"
    return stderr.decode(errors="replace") or f"No matches found for pattern: {pattern}"
