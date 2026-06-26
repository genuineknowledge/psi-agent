"""Bash tool for executing shell commands asynchronously."""

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


async def tool(command: str, timeout_seconds: int = 30) -> str:
    """Execute a shell command and return its output.

    Args:
        command: Shell command to execute.
        timeout_seconds: Maximum seconds to wait for the command to complete.

    Returns:
        Combined stdout and stderr output as a string.
    """
    bash = _find_bash()
    if not bash:
        return (
            "Error: bash executable was not found on PATH. Install Git Bash, WSL, or bash before using this workspace."
        )

    process = await asyncio.create_subprocess_exec(
        bash,
        "-lc",
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
