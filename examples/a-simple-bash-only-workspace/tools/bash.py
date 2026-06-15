"""Async bash tool for executing shell commands."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path


def _find_bash() -> str | None:
    if os.name == "nt":
        candidates: list[Path] = []
        git = shutil.which("git")
        if git:
            git_root = Path(git).resolve().parents[1]
            candidates.extend([git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"])
        candidates.extend(
            [
                Path("C:/Program Files/Git/bin/bash.exe"),
                Path("C:/Program Files/Git/usr/bin/bash.exe"),
                Path("D:/Program Files/Git/bin/bash.exe"),
                Path("D:/Program Files/Git/usr/bin/bash.exe"),
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    return shutil.which("bash")


async def tool(command: str, timeout_seconds: int = 30, cwd: str | None = None) -> str:
    """Execute a bash command asynchronously.

    Args:
        command: The bash command to execute.
        timeout_seconds: Timeout in seconds. Defaults to 30.
        cwd: Working directory for the command. Defaults to None.

    Returns:
        Command output as string, or error message if execution fails.
    """
    try:
        bash = _find_bash()
        if not bash:
            return (
                "Error: bash executable was not found on PATH. "
                "Install Git Bash, WSL, or bash before using this workspace."
            )

        working_dir = Path(cwd) if cwd else None

        process = await asyncio.create_subprocess_exec(
            bash,
            "-lc",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )

        if process.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace")
            return f"Error (exit code {process.returncode}): {detail}"
        return stdout.decode()
    except TimeoutError:
        return f"Error: Command timed out after {timeout_seconds} seconds"
    except Exception as e:
        return f"Error: {e}"
