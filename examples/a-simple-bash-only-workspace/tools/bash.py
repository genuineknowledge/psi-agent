"""Async bash tool for executing shell commands."""

from __future__ import annotations

import asyncio

import anyio


async def tool(command: str, timeout: int = 30, cwd: str | None = None) -> str:
    """Execute a bash command asynchronously.

    Args:
        command: The bash command to execute.
        timeout: Timeout in seconds. Defaults to 30.
        cwd: Working directory for the command. Defaults to None.

    Returns:
        Command output as string, or error message if execution fails.
    """
    try:
        working_dir = anyio.Path(cwd) if cwd else None

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        if process.returncode != 0:
            return f"Error (exit code {process.returncode}): {stderr.decode()}"
        return stdout.decode()
    except TimeoutError:
        return f"Error: Command timed out after {timeout} seconds"
    except Exception as e:
        return f"Error: {e}"
