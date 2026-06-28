"""Execute bash commands."""

from __future__ import annotations

import inspect
import os
import shutil
from pathlib import Path

import anyio
from loguru import logger


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


async def bash(command: str, *, cwd: str | None = None) -> str:
    """Execute a bash command and return the combined stdout and stderr output.

    Args:
        command: The bash command to execute. Use with caution.
        cwd: Working directory. Defaults to the workspace root.
    """
    if cwd is None:
        cwd = str(Path(inspect.getfile(bash)).parent.parent)

    bash_exe = _find_bash()
    if not bash_exe:
        return (
            "Error: bash executable was not found on PATH. Install Git Bash, WSL, or bash before using this workspace."
        )

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
