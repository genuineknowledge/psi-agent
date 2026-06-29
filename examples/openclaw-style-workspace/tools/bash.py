"""Bash tool for executing shell commands."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import anyio


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
        command: The shell command to execute.
        timeout_seconds: Maximum seconds to wait for the command to complete.

    Returns:
        Combined stdout and stderr output, with exit code appended on failure.
    """
    bash = _find_bash()
    if not bash:
        return (
            "[Error] bash executable was not found on PATH. Install Git Bash, WSL, or bash before using this workspace."
        )

    try:
        with anyio.fail_after(timeout_seconds):
            result = await anyio.run_process([bash, "-lc", command], check=False)
    except TimeoutError:
        return f"[Error] Command timed out after {timeout_seconds}s: {command}"

    out = result.stdout.decode(errors="replace")
    err = result.stderr.decode(errors="replace")
    combined = (out + err).rstrip()

    if result.returncode != 0:
        combined += f"\n[Exit code: {result.returncode}]"

    return combined or "(no output)"
