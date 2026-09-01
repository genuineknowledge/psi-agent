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
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _runtime_paths as _paths
import anyio
import anyio.to_thread
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
    """Prefer Session ContextVar / WORKSPACE_DIR (see ``_runtime_paths``)."""
    return _paths.resolve_workspace(raw)


def registry_path(workspace: anyio.Path) -> anyio.Path:
    return workspace / ".psi" / "background" / "registry.json"


# A ``process_id`` reaches us from the model and becomes a *filename*, so it is
# validated rather than sanitized: quietly rewriting it would let two different
# ids collapse onto one log, and `..` would walk out of the directory.
_ID_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def invalid_process_id(process_id: str) -> str:
    """Return why *process_id* is unusable as a log filename, or ``""``."""
    if process_id in (".", ".."):
        return "process_id must not be '.' or '..'"
    bad = sorted(set(process_id) - _ID_SAFE)
    if bad:
        return f"process_id may only contain letters, digits, '.', '_' and '-'; found {''.join(bad)!r}"
    return ""


def log_path_for(workspace: anyio.Path, process_id: str) -> anyio.Path:
    """Where a background process's combined output lands.

    Derived from the id alone, never read out of the registry: a finished
    process is pruned from the registry by ``_prune_dead_unlocked``, and
    "it finished, show me what it produced" is the common case — so the
    output has to stay reachable after the record is gone.
    """
    return workspace / ".psi" / "background" / f"{process_id}.log"


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


def _open_log_sink(log_file: Path) -> Any:
    """Open *log_file* as the child's stdout/stderr sink.

    A plain OS file handle, not a pipe: the parent must not have to stay
    around draining it. That is the whole point of a detached process —
    a pipe with nobody reading it fills its buffer and blocks the child
    somewhere around 64KB, which for a paging script means it stalls
    partway through and looks like a hang.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    return log_file.open("wb")


async def _spawn_detached(argv: list[str], *, cwd: str, log_file: Path) -> Any:
    logger.debug(f"background spawning cwd={cwd!r} argv={argv!r} log={str(log_file)!r}")
    sink = await anyio.to_thread.run_sync(_open_log_sink, log_file)
    try:
        if sys.platform == "win32":
            return await anyio.open_process(
                argv,
                cwd=cwd,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB,
            )
        return await anyio.open_process(
            argv,
            cwd=cwd,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        # Ours to close once the child has inherited it; the child keeps
        # writing through its own copy of the descriptor.
        with suppress(OSError):
            sink.close()


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
    if reason := invalid_process_id(bg_id):
        return {
            "ok": False,
            "status": "failed",
            "message": reason,
            "process_id": "",
            "pid": 0,
        }

    log_file = Path(str(log_path_for(workspace, bg_id)))
    try:
        process = await _spawn_detached(argv, cwd=workdir, log_file=log_file)
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
        # Report the log even here: a command that dies on startup usually
        # printed the reason, and this used to surface as a bare "exited
        # immediately" with the explanation discarded to DEVNULL.
        return {
            "ok": False,
            "status": "failed",
            "message": "process exited immediately after spawn — read log_path for its output",
            "process_id": bg_id,
            "pid": pid,
            "log_path": str(log_file),
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
        "log_path": str(log_file),
    }

    async def _register(registry: dict[str, Any]) -> None:
        processes = registry.setdefault("processes", {})
        if not isinstance(processes, dict):
            registry["processes"] = {}
            processes = registry["processes"]
        processes[bg_id] = record

    await _update_registry(workspace, _register)

    logger.info(f"background started process_id={bg_id!r} pid={pid} shell={shell_name!r} log={str(log_file)!r}")
    return {
        "ok": True,
        "status": "running",
        "message": f"started — read its output with background_output({bg_id!r})",
        "process_id": bg_id,
        "pid": pid,
        "shell": shell_name,
        "cwd": workdir,
        "workspace": str(workspace),
        "log_path": str(log_file),
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
                    "log_path": rec.get("log_path", ""),
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


async def read_output(
    *,
    process_id: str,
    workspace_raw: str = "",
    tail_lines: int = 200,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """Read what a background process has written so far.

    Reads the log by *id*, not through the registry, because a finished
    process is pruned from the registry the next time anything touches it —
    and "it finished, what did it produce?" is exactly when this is called.
    ``alive`` therefore reports process state independently of whether a
    record still exists.

    The tail is taken rather than the head: a long run's useful end is where
    it stopped, and the beginning is usually setup noise.
    """
    workspace = resolve_workspace(workspace_raw)
    pid_key = process_id.strip()
    if not pid_key:
        return {"ok": False, "message": "process_id must not be empty", "process_id": ""}
    if reason := invalid_process_id(pid_key):
        return {"ok": False, "message": reason, "process_id": pid_key}

    log_file = log_path_for(workspace, pid_key)
    record = await _lookup_record(workspace, pid_key)
    pid = _registry_pid(record) if record else 0
    alive = _pid_alive(pid) if pid else False

    if not await log_file.exists():
        return {
            "ok": False,
            "message": (
                f"no output log for {pid_key!r}. Either the id is wrong, or it was started "
                "before output capture existed — check background_list."
            ),
            "process_id": pid_key,
            "alive": alive,
            "log_path": str(log_file),
        }

    raw = await log_file.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    truncated_lines = max(0, len(lines) - tail_lines) if tail_lines > 0 else 0
    text = "\n".join(lines[-tail_lines:]) if tail_lines > 0 else raw

    truncated_chars = 0
    if max_chars > 0 and len(text) > max_chars:
        truncated_chars = len(text) - max_chars
        text = text[-max_chars:]

    return {
        "ok": True,
        "process_id": pid_key,
        "pid": pid,
        # Still running: the output is a snapshot and reading again will show more.
        "alive": alive,
        "log_path": str(log_file),
        "total_lines": len(lines),
        "omitted_leading_lines": truncated_lines,
        "omitted_leading_chars": truncated_chars,
        "output": text,
    }


async def _lookup_record(workspace: anyio.Path, process_id: str) -> dict[str, Any] | None:
    """Fetch a registry record without pruning — reading must not mutate state."""
    registry = await _read_registry(registry_path(workspace))
    processes = registry.get("processes")
    if not isinstance(processes, dict):
        return None
    rec = processes.get(process_id)
    return rec if isinstance(rec, dict) else None
