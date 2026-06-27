"""Execute bash commands."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import anyio
from loguru import logger


async def bash(command: str, *, cwd: str | None = None) -> str:
    """Execute a bash command and return the combined stdout and stderr output.

    Args:
        command: The bash command to execute. Use with caution.
        cwd: Working directory. Defaults to the workspace root.
    """
    if cwd is None:
        cwd = str(Path(inspect.getfile(bash)).parent.parent)

    logger.info(f"Executing bash command: {command} (cwd={cwd})")
    bash_exe = shutil.which("bash") or "/bin/bash"
    try:
        result = await anyio.run_process([bash_exe, "-c", command], cwd=cwd)
        stdout = result.stdout.decode().strip()
        stderr = result.stderr.decode().strip()
        output = stdout
        if stderr:
            output += f"\n[stderr]\n{stderr}"
        output = output.strip() or "(no output)"
        logger.debug(f"Bash result: {output[:200]}")
        return output
    except Exception as e:
        logger.error(f"Bash command failed: {e}")
        return f"Error executing command: {e}"
