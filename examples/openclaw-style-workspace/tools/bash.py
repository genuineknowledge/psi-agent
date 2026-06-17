"""Bash tool — execute shell commands."""

from __future__ import annotations

import asyncio


async def bash(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to execute.
        timeout: Maximum seconds to wait for the command to complete.

    Returns:
        Combined stdout and stderr output, with exit code appended on failure.
    """
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return f"[Error] Command timed out after {timeout}s: {command}"

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    combined = (out + err).rstrip()

    if process.returncode != 0:
        combined += f"\n[Exit code: {process.returncode}]"

    return combined or "(no output)"
