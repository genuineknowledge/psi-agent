"""Background process registry — spawn, track, and stop detached OS processes.

Registry: <workspace>/.psi/background/registry.json
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

if sys.platform == "win32":
    import ctypes

    _STILL_ACTIVE = 259
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_registry_locks: dict[str, anyio.Lock] = {}
_registry_locks_guard = anyio.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def resolve_workspace(raw: str) -> anyio.Path:
    if raw.strip():
        return anyio.Path(raw.strip())
    env = os.environ.get("WORKSPACE_DIR", "").strip()
    if env:
        return anyio.Path(env)
    return anyio.Path(str(Path(__file__).resolve().parents[1]))


def registry_path(workspace: anyio.Path) -> anyio.Path:
    return workspace / ".psi" / "background" / "registry.json"


def _pool_key(workspace: anyio.Path) -> str:
    return str(workspace)


async def _get_registry_lock(workspace: anyio.Path) -> anyio.Lock:
    key = _pool_key(workspace)
    async with _registry_locks_guard:
        lock = _registry_locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            _registry_locks[key] = lock
        return lock


@asynccontextmanager
async def _registry_critical(workspace: anyio.Path):
    lock = await _get_registry_lock(workspace)
    async with lock:
        yield


async def _update_registry[T](
    workspace: anyio.Path,
    mutator: Callable[[dict[str, Any]], Awaitable[T]],
) -> T:
    path = registry_path(workspace)
    async with _registry_critical(workspace):
        registry = await _read_registry(path)
        result = await mutator(registry)
        await _write_registry(path, registry)
        return result


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


def _find_powershell() -> str:
    found = shutil.which("pwsh") or shutil.which("powershell")
    if found:
        return found
    for path in (
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ):
        if Path(path).is_file():
            return path
    return "powershell"


def shell_argv(command: str, *, shell: str = "auto") -> tuple[list[str], str]:
    """Build argv for a detached shell running *command*."""
    command = command.strip()
    if not command:
        msg = "command must not be empty"
        raise ValueError(msg)
    mode = shell.strip().lower() or "auto"
    if mode in ("powershell", "pwsh"):
        pwsh = _find_powershell()
        return [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ], "powershell"
    if mode == "bash":
        bash = _find_bash()
        if not bash:
            msg = "bash executable was not found"
            raise ValueError(msg)
        return [bash, "-lc", command], "bash"
    bash = _find_bash()
    if bash:
        return [bash, "-lc", command], "bash"
    if sys.platform == "win32":
        pwsh = _find_powershell()
        return [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-OutputFormat",
            "Text",
            "-Command",
            command,
        ], "powershell"
    return ["sh", "-c", command], "sh"


def _default_cwd(workspace: anyio.Path) -> str:
    return str(workspace)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        if not ok:
            return False
        return exit_code.value == _STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_safe_registry_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        logger.error(f"background refuse to terminate own pid={pid}")
        return False
    try:
        parent = os.getppid()
    except OSError:
        parent = 0
    if parent > 0 and pid == parent:
        logger.error(f"background refuse to terminate parent pid={pid}")
        return False
    return True


def _sync_taskkill_pid(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/PID", str(pid)],
        check=False,
        capture_output=True,
    )


async def _terminate_pid(pid: int) -> None:
    if not _is_safe_registry_pid(pid) or not _pid_alive(pid):
        return
    logger.info(f"background registry terminating pid={pid} (single process only)")
    if sys.platform == "win32":
        await anyio.to_thread.run_sync(_sync_taskkill_pid, pid)
        for _ in range(10):
            if not _pid_alive(pid):
                return
            await anyio.sleep(0.1)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    await anyio.sleep(0.4)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return


async def _spawn_detached(argv: list[str], *, cwd: str) -> Any:
    logger.debug(f"background spawning cwd={cwd!r} argv={argv!r}")
    if sys.platform == "win32":
        return await anyio.open_process(
            argv,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB,
        )
    return await anyio.open_process(
        argv,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def _read_registry(path: anyio.Path) -> dict[str, Any]:
    if not await path.exists():
        return {"processes": {}}
    try:
        raw = await path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        return {"processes": {}}
    if not isinstance(data, dict):
        return {"processes": {}}
    processes = data.get("processes")
    return {"processes": processes if isinstance(processes, dict) else {}}


async def _write_registry(path: anyio.Path, data: dict[str, Any]) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if await path.exists():
        await path.unlink()
    await tmp.rename(path)


def _registry_pid(rec: object) -> int:
    if not isinstance(rec, dict):
        return 0
    raw = rec.get("pid", 0)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _prune_dead_unlocked(registry: dict[str, Any]) -> list[str]:
    processes = registry.get("processes")
    if not isinstance(processes, dict):
        registry["processes"] = {}
        return []
    removed: list[str] = []
    for pid_key, rec in list(processes.items()):
        if not isinstance(rec, dict):
            processes.pop(pid_key, None)
            removed.append(str(pid_key))
            continue
        pid = _registry_pid(rec)
        if not _pid_alive(pid):
            processes.pop(pid_key, None)
            removed.append(str(pid_key))
    return removed


async def start_process(
    *,
    command: str,
    workspace_raw: str = "",
    cwd: str = "",
    process_id: str = "",
    shell: str = "auto",
) -> dict[str, Any]:
    workspace = resolve_workspace(workspace_raw)
    if not await workspace.exists():
        return {
            "ok": False,
            "status": "failed",
            "message": f"Workspace not found: {workspace}",
            "process_id": process_id or "",
            "pid": 0,
        }

    command = command.strip()
    if not command:
        return {
            "ok": False,
            "status": "failed",
            "message": "command must not be empty",
            "process_id": "",
            "pid": 0,
        }

    try:
        argv, shell_name = shell_argv(command, shell=shell)
    except ValueError as exc:
        return {
            "ok": False,
            "status": "failed",
            "message": str(exc),
            "process_id": "",
            "pid": 0,
        }

    workdir = cwd.strip() or _default_cwd(workspace)
    bg_id = process_id.strip() or f"bg-{uuid.uuid4().hex[:16]}"

    try:
        process = await _spawn_detached(argv, cwd=workdir)
    except Exception as exc:
        logger.warning(f"background spawn failed: {exc}")
        return {
            "ok": False,
            "status": "failed",
            "message": str(exc),
            "process_id": bg_id,
            "pid": 0,
        }

    pid = int(process.pid or 0)
    await anyio.sleep(0.2)
    if not _pid_alive(pid):
        return {
            "ok": False,
            "status": "failed",
            "message": "process exited immediately after spawn",
            "process_id": bg_id,
            "pid": pid,
        }

    now = _iso(_utc_now())
    record = {
        "process_id": bg_id,
        "pid": pid,
        "command": command,
        "cwd": workdir,
        "shell": shell_name,
        "argv": argv,
        "workspace": str(workspace),
        "created_at": now,
    }

    async def _register(registry: dict[str, Any]) -> None:
        processes = registry.setdefault("processes", {})
        if not isinstance(processes, dict):
            registry["processes"] = {}
            processes = registry["processes"]
        processes[bg_id] = record

    await _update_registry(workspace, _register)

    logger.info(f"background started process_id={bg_id!r} pid={pid} shell={shell_name!r}")
    return {
        "ok": True,
        "status": "running",
        "message": "started",
        "process_id": bg_id,
        "pid": pid,
        "shell": shell_name,
        "cwd": workdir,
        "workspace": str(workspace),
    }


async def stop_process(*, process_id: str, workspace_raw: str = "") -> dict[str, Any]:
    workspace = resolve_workspace(workspace_raw)
    pid_key = process_id.strip()
    if not pid_key:
        return {
            "ok": False,
            "status": "failed",
            "message": "process_id must not be empty",
            "process_id": "",
        }

    async def _stop(registry: dict[str, Any]) -> dict[str, Any] | None:
        _prune_dead_unlocked(registry)
        processes = registry.get("processes")
        if not isinstance(processes, dict):
            return None
        rec = processes.pop(pid_key, None)
        return rec if isinstance(rec, dict) else None

    rec = await _update_registry(workspace, _stop)
    if rec is None:
        return {
            "ok": False,
            "status": "not_found",
            "message": f"process not found or already stopped: {pid_key!r}",
            "process_id": pid_key,
        }

    pid = _registry_pid(rec)
    logger.info(f"background stop process_id={pid_key!r} pid={pid}")
    await _terminate_pid(pid)
    still_alive = _pid_alive(pid)
    return {
        "ok": not still_alive,
        "status": "stopped" if not still_alive else "stop_requested",
        "message": "stopped" if not still_alive else f"stop sent but pid {pid} may still be running",
        "process_id": pid_key,
        "pid": pid,
    }


async def list_processes(*, workspace_raw: str = "") -> dict[str, Any]:
    workspace = resolve_workspace(workspace_raw)

    async def _list(registry: dict[str, Any]) -> list[dict[str, Any]]:
        pruned = _prune_dead_unlocked(registry)
        processes = registry.get("processes")
        if not isinstance(processes, dict):
            return []
        rows: list[dict[str, Any]] = []
        for bg_id, rec in processes.items():
            if not isinstance(rec, dict):
                continue
            pid = _registry_pid(rec)
            rows.append(
                {
                    "process_id": bg_id,
                    "pid": pid,
                    "alive": _pid_alive(pid),
                    "command": rec.get("command", ""),
                    "cwd": rec.get("cwd", ""),
                    "shell": rec.get("shell", ""),
                    "created_at": rec.get("created_at", ""),
                }
            )
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        if pruned:
            logger.debug(f"background pruned dead process ids: {pruned}")
        return rows

    rows = await _update_registry(workspace, _list)
    return {
        "ok": True,
        "workspace": str(workspace),
        "processes": rows,
    }
