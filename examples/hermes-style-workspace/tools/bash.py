"""Bash tool for executing shell commands asynchronously."""

from __future__ import annotations

import asyncio


async def tool(command: str, timeout_seconds: int = 30) -> str:
    """Execute a shell command and return its output.

    Args:
        command: Shell command to execute.
        timeout_seconds: Maximum seconds to wait for the command to complete.

    Returns:
        Combined stdout and stderr output as a string.
    """
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        return f"Error: command timed out after {timeout_seconds} seconds"

    output = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if err:
        output = output + ("\n" if output else "") + err

    return output.strip()
