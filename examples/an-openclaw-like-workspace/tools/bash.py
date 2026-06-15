"""Execute shell commands asynchronously."""

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


async def tool(command: str, timeout_seconds: int = 30) -> dict[str, str | int]:
    """Execute a shell command asynchronously.

    Args:
        command: The shell command to execute.
        timeout_seconds: Maximum time in seconds to wait for the command.
            Defaults to 30 seconds.

    Returns:
        Dictionary with stdout, stderr, and exit_code fields.
        If timeout occurs, returns error message in stderr.
    """
    try:
        bash = _find_bash()
        if not bash:
            return {
                "stdout": "",
                "stderr": (
                    "Error: bash executable was not found on PATH. "
                    "Install Git Bash, WSL, or bash before using this workspace."
                ),
                "exit_code": -1,
            }

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
            await process.wait()
            return {
                "stdout": "",
                "stderr": f"Error: Command timed out after {timeout_seconds} seconds",
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
