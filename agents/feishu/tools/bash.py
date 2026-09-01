"""Bash tool for executing shell commands."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import _proc_run
import _runtime_paths as _paths


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


async def bash(command: str, timeout_seconds: int = 30) -> str:
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

    # Force UTF-8 for any Python (and most CLIs) spawned by the command. On Windows
    # the child would otherwise default to the system codepage (GBK/cp936), so any
    # non-ASCII stdout comes back as mojibake once we decode it as UTF-8 below. On
    # Linux/macOS the locale is already UTF-8, so this is a harmless no-op.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    cwd = _paths.workspace_dir()

    result = await _proc_run.run_capturing(
        [bash, "-lc", command],
        timeout_seconds=timeout_seconds,
        env=env,
        cwd=cwd,
    )
    combined = (result.stdout + result.stderr).rstrip()

    if result.timed_out:
        # Report the partial output rather than dropping it: a bare "timed out"
        # tells the agent nothing about where the command stalled, and the only
        # thing it can do with that is retry at a higher limit. Real sessions
        # show the same command retried at 60/90/100/120/180s for that reason.
        head = f"[Error] Command timed out after {timeout_seconds}s: {command}"
        if result.orphaned:
            head += "\n[Warning] The process survived termination and may still be running."
        if not combined:
            return f"{head}\n(no output was produced before the timeout)"
        return f"{head}\n--- output produced before the timeout ---\n{combined}"

    if result.returncode != 0:
        combined += f"\n[Exit code: {result.returncode}]"
        return combined.lstrip("\n")

    # Succeeding silently and producing nothing are different outcomes; the old
    # shared "(no output)" made the agent re-run commands to tell them apart.
    return combined or "(exit code 0, no output)"
