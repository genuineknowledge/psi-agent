"""Execute shell commands asynchronously."""

from __future__ import annotations

import asyncio


async def tool(command: str, timeout: int = 30) -> dict[str, str | int]:
    """Execute a shell command asynchronously.

    Args:
        command: The shell command to execute.
        timeout: Maximum time in seconds to wait for the command.
            Defaults to 30 seconds.

    Returns:
        Dictionary with stdout, stderr, and exit_code fields.
        If timeout occurs, returns error message in stderr.
    """
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "stdout": "",
                "stderr": f"Error: Command timed out after {timeout} seconds",
                "exit_code": -1,
            }

        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode if process.returncode is not None else -1,
        }

    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Error: Failed to execute command: {e}",
            "exit_code": -1,
        }
