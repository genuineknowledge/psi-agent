"""Load an external agent system module through a frozen psi-agent executable.

The workspace system is compiled and executed at runtime, so source-level hook
tests cannot catch a missing PyInstaller hidden import. This probe starts the
frozen ``session`` command, waits for its hook-resolution log, and exits before
any model request is made.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = (
    "system_prompt_builder",
    "system_prompt_rebuild_checker",
    "compact_history",
    "turn_context_builder",
    "system_before_turn",
    "system_after_turn",
)
_HOOK_STATUS = re.compile(r"(?P<name>[a-z_]+)=(?P<status>loaded|missing)")
_LOG_MARKER = "System hooks loaded from "


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _hook_statuses(log: str) -> dict[str, str] | None:
    for line in reversed(log.splitlines()):
        if _LOG_MARKER not in line:
            continue
        statuses = {match.group("name"): match.group("status") for match in _HOOK_STATUS.finditer(line)}
        if all(name in statuses for name in HOOKS):
            return statuses
    return None


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True, help="Path to the frozen psi-agent executable")
    parser.add_argument("--agent", type=Path, required=True, help="Agent package containing systems/system.py")
    parser.add_argument("--timeout", type=float, default=90.0, help="Startup timeout in seconds")
    args = parser.parse_args()

    exe = args.exe.resolve()
    agent = args.agent.resolve()
    system_py = agent / "systems" / "system.py"
    if not exe.is_file():
        raise SystemExit(f"Frozen executable not found: {exe}")
    if not system_py.is_file():
        raise SystemExit(f"Agent system module not found: {system_py}")

    with tempfile.TemporaryDirectory(prefix="psi-agent-pyinstaller-smoke-") as temp_dir:
        root = Path(temp_dir)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "schedules").mkdir()
        appdata = root / "appdata"
        log_path = root / "session.log"
        command = [
            str(exe),
            "session",
            "--ai-socket",
            f"http://127.0.0.1:{_free_port()}",
            "--channel-socket",
            f"http://127.0.0.1:{_free_port()}",
            "--workspace",
            str(workspace),
            "--agent",
            str(agent),
            "--appdata",
            str(appdata),
            "--verbose",
        ]
        environment = os.environ.copy()
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                cwd=exe.parent,
                env=environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                deadline = time.monotonic() + args.timeout
                statuses: dict[str, str] | None = None
                while time.monotonic() < deadline:
                    log = _read_log(log_path)
                    statuses = _hook_statuses(log)
                    if statuses is not None:
                        missing = [name for name in HOOKS if statuses[name] != "loaded"]
                        if missing:
                            raise SystemExit(
                                f"Frozen executable loaded {system_py} with missing hooks: {missing}\n{log}"
                            )
                        print(f"PyInstaller system smoke passed: {system_py}")
                        print(" ".join(f"{name}=loaded" for name in HOOKS))
                        return 0
                    if process.poll() is not None:
                        raise SystemExit(
                            f"Frozen executable exited with code {process.returncode} before loading hooks.\n{log}"
                        )
                    time.sleep(0.25)
                raise SystemExit(f"Timed out waiting for hook resolution in frozen executable.\n{_read_log(log_path)}")
            finally:
                _stop(process)


if __name__ == "__main__":
    sys.exit(main())
