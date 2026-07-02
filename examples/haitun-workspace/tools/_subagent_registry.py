"""Background subagent process registry for workspace-local three-part stacks.

Each subagent gets its own spawned ``psi-agent ai`` + ``psi-agent session`` (never shares
the parent Session or Gateway AI pipe). Only AI *credentials* are inherited from the parent
process environment (provider, model, api_key, base_url).
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
import anyio
from aiohttp import ClientTimeout
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk

DEFAULT_IDLE_SECONDS = 1800.0
DEFAULT_RUN_TIMEOUT_SECONDS = 600.0
MIN_IDLE_SECONDS = 60.0
MAX_IDLE_SECONDS = 86400.0
MIN_RUN_TIMEOUT_SECONDS = 30.0
MAX_RUN_TIMEOUT_SECONDS = 3600.0

_registry_locks: dict[str, anyio.Lock] = {}
_registry_locks_guard = anyio.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def idle_seconds_from_env() -> float:
    raw = os.environ.get("PSI_SUBAGENT_IDLE_SECONDS")
    if raw is None:
        return DEFAULT_IDLE_SECONDS
    try:
        parsed = float(raw)
    except TypeError, ValueError:
        return DEFAULT_IDLE_SECONDS
    if parsed <= 0:
        return DEFAULT_IDLE_SECONDS
    return _clamp(parsed, MIN_IDLE_SECONDS, MAX_IDLE_SECONDS)


def resolve_workspace(raw: str) -> anyio.Path:
    if raw.strip():
        return anyio.Path(raw.strip())
    env = os.environ.get("WORKSPACE_DIR", "").strip()
    if env:
        return anyio.Path(env)
    return anyio.Path(str(Path(__file__).resolve().parents[1]))


def registry_path(workspace: anyio.Path) -> anyio.Path:
    return workspace / ".psi" / "subagent" / "registry.json"


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


def _pool_key(workspace: anyio.Path) -> str:
    return str(workspace)


def _socket_prefix(workspace: anyio.Path) -> str:
    digest = uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex[:12]
    return f"ht-sub-{digest}"


def _channel_socket(prefix: str, session_id: str) -> str:
    if sys.platform == "win32":
        return rf"\\.\pipe\{prefix}\channels\{session_id}"
    return f"/tmp/{prefix}/channels/{session_id}.sock"


def _dedicated_pool_key(workspace: anyio.Path, session_id: str) -> str:
    return f"{_pool_key(workspace)}::ai::{session_id}"


def _ai_socket_for_session(prefix: str, session_id: str) -> str:
    if sys.platform == "win32":
        return rf"\\.\pipe\{prefix}\ai\{session_id}"
    return f"/tmp/{prefix}/ai/{session_id}.sock"


def other_active_subagent_session_ids(
    registry: dict[str, Any],
    workspace: anyio.Path,
    *,
    exclude_session_id: str = "",
) -> list[str]:
    """Return live subagent session ids in *workspace*, excluding *exclude_session_id*."""
    sessions = registry.get("sessions")
    if not isinstance(sessions, dict):
        return []
    ws = str(workspace)
    active: list[str] = []
    for sid, rec in sessions.items():
        if str(sid) == exclude_session_id or not isinstance(rec, dict):
            continue
        if str(rec.get("workspace", "")) != ws:
            continue
        pid = int(rec.get("session_pid", 0) or 0)
        if _pid_alive(pid):
            active.append(str(sid))
    return active


def plan_ai_pool_for_session(
    registry: dict[str, Any],
    workspace: anyio.Path,
    session_id: str,
) -> str:
    """Return the AI pool key for *session_id* (always a per-session spawned ``psi-agent ai``)."""
    sessions = registry.get("sessions")
    if isinstance(sessions, dict):
        existing = sessions.get(session_id)
        if isinstance(existing, dict):
            pool_key = str(existing.get("ai_pool_key", ""))
            if pool_key:
                return pool_key
    return _dedicated_pool_key(workspace, session_id)


async def _ensure_socket_dir(socket: str) -> None:
    if sys.platform != "win32":
        await anyio.Path(socket).parent.mkdir(parents=True, exist_ok=True)


def _resolve_project_root(workspace: anyio.Path) -> Path:
    """Find the nearest ancestor of *workspace* that contains ``pyproject.toml``."""
    start = Path(str(workspace)).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start


def _psi_cmd(workspace: anyio.Path) -> list[str]:
    custom = os.environ.get("PSI_CMD", "").strip()
    if custom:
        return shlex.split(custom)
    root = _resolve_project_root(workspace)
    if sys.platform == "win32":
        venv_exe = root / ".venv" / "Scripts" / "psi-agent.exe"
    else:
        venv_exe = root / ".venv" / "bin" / "psi-agent"
    if venv_exe.is_file():
        return [str(venv_exe)]
    return ["uv", "run", "--project", str(root), "--no-sync", "psi-agent"]


def _spawn_cwd(workspace: anyio.Path) -> str:
    return str(_resolve_project_root(workspace))


def _ai_env() -> tuple[str, str, str, str]:
    """AI credentials inherited from the parent Session process (never its socket)."""
    provider = (os.environ.get("PSI_AI_PROVIDER") or os.environ.get("FLOW_PSI_AI") or "openai").strip()
    model = (os.environ.get("PSI_AI_MODEL") or os.environ.get("FLOW_PSI_MODEL") or "").strip()
    api_key = (
        os.environ.get("PSI_AI_API_KEY") or os.environ.get("FLOW_PSI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    ).strip()
    base_url = (os.environ.get("PSI_AI_BASE_URL") or os.environ.get("FLOW_PSI_BASE_URL") or "").strip()
    return provider, model, api_key, base_url


async def _spawn_psi_process(cmd: list[str], *, cwd: str) -> anyio.abc.Process:
    logger.debug(f"subagent spawning process cwd={cwd!r} cmd={cmd!r}")
    return await anyio.open_process(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


async def _wait_socket_or_raise(process: anyio.abc.Process, socket: str, *, timeout_sec: float = 30.0) -> None:
    try:
        await _wait_socket(socket, timeout_sec=timeout_sec)
    except Exception as exc:
        detail = ""
        if process.stderr is not None:
            try:
                with anyio.fail_after(1.0):
                    raw = await process.stderr.receive()
                if raw:
                    detail = raw.decode("utf-8", errors="replace").strip()[:500]
            except Exception:
                pass
        if _pid_alive(process.pid):
            await _terminate_pid(process.pid)
        msg = f"Socket not ready after {timeout_sec}s: {socket}"
        if detail:
            msg = f"{msg} (stderr: {detail})"
        raise RuntimeError(msg) from exc


def standalone_ai_configured() -> bool:
    """True when subagent may spawn its own ``psi-agent ai`` (API key or local base URL)."""
    _provider, _model, api_key, base_url = _ai_env()
    return bool(api_key or base_url)


def ai_pool_missing_message() -> str:
    return (
        "No usable AI credentials for subagent. "
        "Set parent process env before starting Gateway/Session: "
        "OPENAI_API_KEY or FLOW_PSI_API_KEY (+ FLOW_PSI_MODEL, FLOW_PSI_BASE_URL, FLOW_PSI_AI as needed)."
    )


def ai_pool_owns_process(pool: dict[str, Any]) -> bool:
    """Return True when stop may terminate the spawned ``psi-agent ai`` process."""
    if pool.get("from_parent") or pool.get("from_gateway"):
        return False
    return int(pool.get("ai_pid", 0) or 0) > 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def _probe_socket(socket: str, timeout_sec: float = 2.0) -> bool:
    """Return True when the channel socket accepts HTTP (server still listening)."""
    if not socket:
        return False
    if sys.platform == "win32":
        connector: aiohttp.BaseConnector = aiohttp.NamedPipeConnector(path=socket)
    else:
        connector = aiohttp.UnixConnector(path=socket)
    try:
        with anyio.fail_after(timeout_sec):
            async with aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=2.0)) as session:
                async with session.get("http://localhost/") as _resp:
                    pass
        return True
    except Exception:
        return False


async def _wait_socket(socket: str, timeout_sec: float = 30.0) -> None:
    if sys.platform == "win32":
        connector: aiohttp.BaseConnector = aiohttp.NamedPipeConnector(path=socket)
    else:
        connector = aiohttp.UnixConnector(path=socket)
    deadline = anyio.current_time() + timeout_sec
    async with aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=2.0)) as session:
        while anyio.current_time() < deadline:
            try:
                async with session.get("http://localhost/") as _resp:
                    pass
                return
            except Exception:
                await anyio.sleep(0.2)
    msg = f"Socket not ready after {timeout_sec}s: {socket}"
    raise RuntimeError(msg)


def _sync_taskkill_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        check=False,
        capture_output=True,
    )


async def _terminate_pid(pid: int) -> None:
    if pid <= 0 or not _pid_alive(pid):
        return
    logger.info(f"subagent registry terminating pid={pid}")
    if sys.platform == "win32":
        await anyio.to_thread.run_sync(_sync_taskkill_tree, pid)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    await anyio.sleep(0.4)
    if _pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                return


async def _read_registry(path: anyio.Path) -> dict[str, Any]:
    if not await path.exists():
        return {"ai_pools": {}, "sessions": {}}
    try:
        raw = await path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        return {"ai_pools": {}, "sessions": {}}
    if not isinstance(data, dict):
        return {"ai_pools": {}, "sessions": {}}
    pools = data.get("ai_pools")
    sessions = data.get("sessions")
    return {
        "ai_pools": pools if isinstance(pools, dict) else {},
        "sessions": sessions if isinstance(sessions, dict) else {},
    }


async def _write_registry(path: anyio.Path, data: dict[str, Any]) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if await path.exists():
        await path.unlink()
    await tmp.rename(path)


def select_idle_session_ids(
    registry: dict[str, Any],
    *,
    now: datetime,
    idle_seconds: float,
) -> list[str]:
    """Return session ids whose last_used_at is older than *idle_seconds*."""
    sessions = registry.get("sessions")
    if not isinstance(sessions, dict):
        return []
    stale: list[str] = []
    for sid, rec in sessions.items():
        if not isinstance(rec, dict):
            continue
        last_used = _parse_iso(str(rec.get("last_used_at", "")))
        if last_used is None:
            stale.append(str(sid))
            continue
        if (now - last_used).total_seconds() >= idle_seconds:
            stale.append(str(sid))
    return stale


async def _stop_session_unlocked(
    registry: dict[str, Any],
    session_id: str,
) -> bool:
    sessions = registry.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        registry["sessions"] = {}
        sessions = registry["sessions"]
    rec = sessions.pop(session_id, None)
    if not isinstance(rec, dict):
        return False

    session_pid = int(rec.get("session_pid", 0) or 0)
    logger.info(f"subagent stop session_id={session_id!r} session_pid={session_pid}")
    await _terminate_pid(session_pid)

    pool_key = str(rec.get("ai_pool_key", ""))
    pools = registry.setdefault("ai_pools", {})
    if isinstance(pools, dict) and pool_key in pools:
        still_used = any(
            isinstance(other, dict) and str(other.get("ai_pool_key", "")) == pool_key for other in sessions.values()
        )
        if not still_used:
            pool = pools.pop(pool_key, None)
            if isinstance(pool, dict):
                if ai_pool_owns_process(pool):
                    await _terminate_pid(int(pool.get("ai_pid", 0) or 0))
                else:
                    logger.info(
                        f"subagent stop skipped shared ai pool key={pool_key!r} socket={pool.get('ai_socket', '')!r}"
                    )
    return True


async def _sweep_idle_sessions_in_registry(
    registry: dict[str, Any],
    *,
    idle_seconds: float,
) -> list[str]:
    now = _utc_now()
    removed: list[str] = []
    for sid in select_idle_session_ids(registry, now=now, idle_seconds=idle_seconds):
        if await _stop_session_unlocked(registry, sid):
            removed.append(sid)
    dead: list[str] = []
    sessions = registry.get("sessions")
    if isinstance(sessions, dict):
        for sid, rec in list(sessions.items()):
            if not isinstance(rec, dict):
                dead.append(str(sid))
                continue
            pid = int(rec.get("session_pid", 0) or 0)
            channel = str(rec.get("channel_socket", ""))
            if not _pid_alive(pid) or not await _probe_socket(channel):
                dead.append(str(sid))
    for sid in dead:
        if await _stop_session_unlocked(registry, sid):
            removed.append(sid)
    return removed


async def sweep_idle_sessions(workspace: anyio.Path, *, idle_seconds: float | None = None) -> list[str]:
    idle = idle_seconds if idle_seconds is not None else idle_seconds_from_env()

    async def _mutate(registry: dict[str, Any]) -> list[str]:
        return await _sweep_idle_sessions_in_registry(registry, idle_seconds=idle)

    return await _update_registry(workspace, _mutate)


async def list_sessions(workspace: anyio.Path) -> list[dict[str, Any]]:
    idle = idle_seconds_from_env()
    now = _utc_now()

    async def _mutate(registry: dict[str, Any]) -> list[dict[str, Any]]:
        await _sweep_idle_sessions_in_registry(registry, idle_seconds=idle)
        sessions = registry.get("sessions")
        if not isinstance(sessions, dict):
            return []
        rows: list[dict[str, Any]] = []
        for sid, rec in sessions.items():
            if not isinstance(rec, dict):
                continue
            last_used = _parse_iso(str(rec.get("last_used_at", "")))
            idle_for = (now - last_used).total_seconds() if last_used else None
            rows.append(
                {
                    "session_id": sid,
                    "workspace": rec.get("workspace", ""),
                    "created_at": rec.get("created_at", ""),
                    "last_used_at": rec.get("last_used_at", ""),
                    "idle_seconds": idle_for,
                    "idle_limit_seconds": idle,
                }
            )
        rows.sort(key=lambda row: str(row.get("last_used_at", "")))
        return rows

    return await _update_registry(workspace, _mutate)


async def stop_session(workspace: anyio.Path, session_id: str) -> bool:
    idle = idle_seconds_from_env()

    async def _mutate(registry: dict[str, Any]) -> bool:
        await _sweep_idle_sessions_in_registry(registry, idle_seconds=idle)
        return await _stop_session_unlocked(registry, session_id)

    return await _update_registry(workspace, _mutate)


async def _spawn_ai_pool(
    pools: dict[str, Any],
    *,
    pool_key: str,
    workspace: anyio.Path,
    prefix: str,
    session_id: str,
) -> dict[str, Any]:
    if not standalone_ai_configured():
        raise RuntimeError(ai_pool_missing_message())

    ai_sock = _ai_socket_for_session(prefix, session_id)
    await _ensure_socket_dir(ai_sock)
    provider, model, api_key, base_url = _ai_env()
    cmd = [*_psi_cmd(workspace), "ai", "--provider", provider, "--session-socket", ai_sock]
    if model:
        cmd += ["--model", model]
    if api_key:
        cmd += ["--api-key", api_key]
    if base_url:
        cmd += ["--base-url", base_url]

    process = await _spawn_psi_process(cmd, cwd=_spawn_cwd(workspace))
    await _wait_socket_or_raise(process, ai_sock)
    record = {"ai_socket": ai_sock, "ai_pid": process.pid, "provider": provider, "model": model}
    pools[pool_key] = record
    logger.info(
        f"subagent spawned ai session_id={session_id!r} pool_key={pool_key!r} "
        f"provider={provider!r} model={model!r} socket={ai_sock!r} pid={process.pid}"
    )
    return record


async def _ensure_ai_pool_for_session(
    registry: dict[str, Any],
    *,
    workspace: anyio.Path,
    prefix: str,
    session_id: str,
) -> tuple[dict[str, Any], str]:
    pools = registry.setdefault("ai_pools", {})
    if not isinstance(pools, dict):
        registry["ai_pools"] = {}
        pools = registry["ai_pools"]

    pool_key = plan_ai_pool_for_session(registry, workspace, session_id)
    existing = pools.get(pool_key)
    if isinstance(existing, dict) and not existing.get("from_parent") and not existing.get("from_gateway"):
        pid = int(existing.get("ai_pid", 0) or 0)
        socket = str(existing.get("ai_socket", ""))
        if socket and _pid_alive(pid):
            return existing, pool_key
        if pid > 0:
            await _terminate_pid(pid)
        pools.pop(pool_key, None)

    record = await _spawn_ai_pool(
        pools,
        pool_key=pool_key,
        workspace=workspace,
        prefix=prefix,
        session_id=session_id,
    )
    return record, pool_key


async def _discard_session_record(registry: dict[str, Any], session_id: str) -> None:
    sessions = registry.get("sessions")
    if not isinstance(sessions, dict):
        return
    rec = sessions.pop(session_id, None)
    if not isinstance(rec, dict):
        return
    await _terminate_pid(int(rec.get("session_pid", 0) or 0))


async def _ensure_session_record(
    registry: dict[str, Any],
    *,
    workspace: anyio.Path,
    prefix: str,
    session_id: str,
    ai_socket: str,
    ai_pool_key: str,
) -> dict[str, Any]:
    sessions = registry.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        registry["sessions"] = {}
        sessions = registry["sessions"]
    existing = sessions.get(session_id)
    if isinstance(existing, dict):
        pid = int(existing.get("session_pid", 0) or 0)
        channel = str(existing.get("channel_socket", ""))
        if channel and _pid_alive(pid) and await _probe_socket(channel):
            return existing
        logger.warning(f"subagent stale session_id={session_id!r} pid={pid} channel={channel!r}; respawning")
        await _discard_session_record(registry, session_id)

    channel_sock = _channel_socket(prefix, session_id)
    await _ensure_socket_dir(channel_sock)
    cmd = [
        *_psi_cmd(workspace),
        "session",
        "--workspace",
        str(workspace),
        "--channel-socket",
        channel_sock,
        "--ai-socket",
        ai_socket,
        "--session-id",
        session_id,
    ]
    process = await _spawn_psi_process(cmd, cwd=_spawn_cwd(workspace))
    await _wait_socket_or_raise(process, channel_sock)
    now = _iso(_utc_now())
    record = {
        "session_id": session_id,
        "workspace": str(workspace),
        "channel_socket": channel_sock,
        "session_pid": process.pid,
        "ai_pool_key": ai_pool_key,
        "created_at": now,
        "last_used_at": now,
    }
    sessions[session_id] = record
    return record


async def _collect_chat(
    channel_socket: str,
    task: str,
    *,
    timeout_seconds: float,
) -> tuple[str, str, list[str]]:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    errors: list[str] = []
    with anyio.fail_after(timeout_seconds):
        async with ChannelCore(session_socket=channel_socket, interval=0.0) as core:
            async with aclosing(core.post([TextChunk(text=task)])) as stream:
                async for chunk in stream:
                    if isinstance(chunk, TextChunk):
                        text_parts.append(chunk.text)
                    elif isinstance(chunk, ReasoningChunk):
                        reasoning_parts.append(chunk.text)
    return "".join(text_parts), "".join(reasoning_parts), errors


async def run_subagent(
    *,
    task: str,
    workspace_raw: str = "",
    session_id: str = "",
    timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    started = anyio.current_time()
    workspace = resolve_workspace(workspace_raw)
    if not await workspace.exists():
        return {
            "ok": False,
            "status": "failed",
            "message": f"Workspace not found: {workspace}",
            "session_id": session_id or "",
            "text": "",
            "workspace": str(workspace),
            "elapsed_seconds": 0.0,
        }

    task = task.strip()
    if not task:
        return {
            "ok": False,
            "status": "failed",
            "message": "task must not be empty",
            "session_id": "",
            "text": "",
            "workspace": str(workspace),
            "elapsed_seconds": 0.0,
        }

    timeout = _clamp(timeout_seconds, MIN_RUN_TIMEOUT_SECONDS, MAX_RUN_TIMEOUT_SECONDS)
    await sweep_idle_sessions(workspace)

    sid = session_id.strip() or f"sub-{uuid.uuid4().hex[:16]}"
    prefix = _socket_prefix(workspace)

    try:

        async def _prepare(registry: dict[str, Any]) -> tuple[str, str, str]:
            pool, pool_key = await _ensure_ai_pool_for_session(
                registry,
                workspace=workspace,
                prefix=prefix,
                session_id=sid,
            )
            ai_socket = str(pool.get("ai_socket", ""))
            record = await _ensure_session_record(
                registry,
                workspace=workspace,
                prefix=prefix,
                session_id=sid,
                ai_socket=ai_socket,
                ai_pool_key=pool_key,
            )
            return str(record.get("channel_socket", "")), ai_socket, pool_key

        channel_socket, ai_socket, ai_pool_key = await _update_registry(workspace, _prepare)

        try:
            text, reasoning, errors = await _collect_chat(channel_socket, task, timeout_seconds=timeout)
            status = "completed"
            ok = True
            message = ""
        except TimeoutError:
            text = ""
            reasoning = ""
            errors = [f"Timed out after {timeout}s"]
            status = "timeout"
            ok = False
            message = errors[0]
        except Exception as exc:
            if sid and session_id.strip():

                async def _respawn(registry: dict[str, Any]) -> str:
                    await _discard_session_record(registry, sid)
                    record = await _ensure_session_record(
                        registry,
                        workspace=workspace,
                        prefix=prefix,
                        session_id=sid,
                        ai_socket=ai_socket,
                        ai_pool_key=ai_pool_key,
                    )
                    return str(record.get("channel_socket", ""))

                logger.warning(f"subagent transport error on {sid!r}, respawning once: {exc}")
                channel_socket = await _update_registry(workspace, _respawn)
                try:
                    text, reasoning, errors = await _collect_chat(channel_socket, task, timeout_seconds=timeout)
                    status = "completed"
                    ok = True
                    message = ""
                except Exception as retry_exc:
                    text = ""
                    reasoning = ""
                    errors = [str(retry_exc)]
                    status = "failed"
                    ok = False
                    message = str(retry_exc)
            else:
                text = ""
                reasoning = ""
                errors = [str(exc)]
                status = "failed"
                ok = False
                message = str(exc)

        async def _touch(registry: dict[str, Any]) -> None:
            sessions = registry.get("sessions")
            if isinstance(sessions, dict) and sid in sessions and isinstance(sessions[sid], dict):
                sessions[sid]["last_used_at"] = _iso(_utc_now())

        await _update_registry(workspace, _touch)

        elapsed = anyio.current_time() - started
        return {
            "ok": ok,
            "status": status,
            "session_id": sid,
            "text": text,
            "reasoning": reasoning,
            "errors": errors,
            "message": message,
            "workspace": str(workspace),
            "elapsed_seconds": round(elapsed, 3),
            "idle_limit_seconds": idle_seconds_from_env(),
        }
    except Exception as exc:
        elapsed = anyio.current_time() - started
        return {
            "ok": False,
            "status": "failed",
            "session_id": sid,
            "text": "",
            "message": str(exc),
            "workspace": str(workspace),
            "elapsed_seconds": round(elapsed, 3),
        }
