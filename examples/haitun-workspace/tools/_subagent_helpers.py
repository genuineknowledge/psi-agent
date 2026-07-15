"""Subagent planning, socket wait, and chat — workspace helpers (not spawn/stop)."""

from __future__ import annotations

import json
import os
import shlex
import socket
import sys
import uuid
from contextlib import aclosing
from pathlib import Path
from typing import Any

import aiohttp
import anyio

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import TextChunk

_GATEWAY_URL_FILE = Path(".psi") / "gateway.url"
_DEFAULT_GATEWAY_URLS = (
    "http://127.0.0.1:62720",
    "http://127.0.0.1:8080",
)


def _read_gateway_url_file(workspace: Path) -> str:
    path = workspace / _GATEWAY_URL_FILE
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text.rstrip("/") if text else ""


def _write_gateway_url_file(workspace: Path, url: str) -> None:
    url = url.strip().rstrip("/")
    if not url:
        return
    path = workspace / _GATEWAY_URL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(url + "\n", encoding="utf-8")


def _gateway_url_from_env() -> str:
    for key in ("PSI_GATEWAY_URL", "GATEWAY_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value.rstrip("/")
    return ""


async def _fetch_gateway_json(url: str, *, timeout_seconds: float = 3.0) -> Any:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.get(url) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def post_gateway_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
) -> Any:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.post(url, json=body) as resp,
    ):
        text = await resp.text()
        if resp.status >= 400:
            msg = f"Gateway HTTP {resp.status}"
            try:
                payload = json.loads(text)
                if isinstance(payload, dict) and payload.get("error"):
                    msg = str(payload["error"])
            except json.JSONDecodeError:
                if text.strip():
                    msg = text.strip()
            raise RuntimeError(msg)
        if not text.strip():
            return {}
        return json.loads(text)


async def _gateway_is_alive(gateway_url: str, *, timeout_seconds: float = 1.5) -> bool:
    try:
        await _fetch_gateway_json(
            f"{gateway_url.rstrip('/')}/openapi.json",
            timeout_seconds=timeout_seconds,
        )
        return True
    except Exception:
        return False


async def resolve_gateway_url(workspace: Path) -> str:
    for candidate in (
        _gateway_url_from_env(),
        _read_gateway_url_file(workspace),
        *_DEFAULT_GATEWAY_URLS,
    ):
        if not candidate:
            continue
        if await _gateway_is_alive(candidate):
            _write_gateway_url_file(workspace, candidate)
            return candidate.rstrip("/")
    return ""


def _workspaces_match(session_workspace: str, workspace: Path) -> bool:
    ws = session_workspace.strip()
    if not ws:
        return False
    try:
        return Path(ws).resolve() == workspace.resolve()
    except OSError:
        return False


async def _resolve_ai_id_for_workspace(
    gateway_url: str,
    *,
    workspace: Path,
    gateway_ai_id: str = "",
) -> str:
    if gateway_ai_id.strip():
        return gateway_ai_id.strip()

    sessions = await _fetch_gateway_json(f"{gateway_url}/sessions")
    if isinstance(sessions, list):
        matches: list[str] = []
        for item in sessions:
            if not isinstance(item, dict):
                continue
            ws = str(item.get("workspace", "")).strip()
            ai_id = str(item.get("ai_id", "")).strip()
            if ai_id and _workspaces_match(ws, workspace):
                matches.append(ai_id)
        if matches:
            return matches[0]

    ais = await _fetch_gateway_json(f"{gateway_url}/ais")
    if isinstance(ais, list) and len(ais) == 1:
        only = ais[0]
        if isinstance(only, dict):
            return str(only.get("id", "")).strip()
    return ""


def _argv_flag(argv: list[str], flag: str) -> str:
    try:
        index = argv.index(flag)
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return argv[index + 1].strip()


def _is_parent_session_process() -> bool:
    return "session" in sys.argv


def resolve_process_parent_ai_binding(workspace: Path) -> dict[str, str] | None:
    """Parent AI binding from this Session process's own CLI argv (three-terminal)."""
    if not _is_parent_session_process():
        return None
    ai_socket = _argv_flag(sys.argv, "--ai-socket")
    if not ai_socket:
        return None
    raw_ws = _argv_flag(sys.argv, "--workspace")
    if raw_ws and not _workspaces_match(raw_ws, workspace):
        return None
    session_id = _argv_flag(sys.argv, "--session-id") or "parent"
    return {
        "ai_id": session_id,
        "ai_socket": ai_socket,
        "provider": "openai",
        "model": "",
    }


def _credentials_from_argv_tokens(tokens: list[str]) -> dict[str, str]:
    if "ai" not in tokens:
        return dict.fromkeys(("provider", "model", "api_key", "base_url"), "")
    provider = _argv_flag(tokens, "--provider") or "openai"
    return {
        "provider": provider,
        "model": _argv_flag(tokens, "--model"),
        "api_key": _argv_flag(tokens, "--api-key"),
        "base_url": _argv_flag(tokens, "--base-url"),
    }


def _credentials_from_shell_command(command: str) -> dict[str, str]:
    if "psi-agent" not in command:
        return dict.fromkeys(("provider", "model", "api_key", "base_url"), "")
    try:
        tokens = shlex.split(command, posix=(sys.platform != "win32"))
    except ValueError:
        return dict.fromkeys(("provider", "model", "api_key", "base_url"), "")
    return _credentials_from_argv_tokens(tokens)


async def _read_background_registry(workspace: Path) -> dict[str, Any]:
    path = anyio.Path(str(workspace)) / ".psi" / "background" / "registry.json"
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


async def credentials_from_background_registry(
    workspace: Path,
    *,
    ai_socket: str = "",
) -> dict[str, str]:
    """Spawn credentials from ``background_start`` registry entries (``psi-agent ai``)."""
    registry = await _read_background_registry(workspace)
    processes = registry.get("processes")
    if not isinstance(processes, dict):
        return dict.fromkeys(("provider", "model", "api_key", "base_url"), "")

    matched: dict[str, str] | None = None
    fallback: dict[str, str] | None = None
    ai_socket = ai_socket.strip()
    for rec in processes.values():
        if not isinstance(rec, dict):
            continue
        command = str(rec.get("command", "")).strip()
        creds = _credentials_from_shell_command(command)
        if not creds.get("model"):
            continue
        if ai_socket:
            try:
                tokens = shlex.split(command, posix=(sys.platform != "win32"))
            except ValueError:
                tokens = []
            if _argv_flag(tokens, "--session-socket") == ai_socket:
                matched = creds
                break
        if fallback is None:
            fallback = creds
    return matched or fallback or dict.fromkeys(("provider", "model", "api_key", "base_url"), "")


async def resolve_parent_ai_binding(
    gateway_url: str,
    *,
    workspace: Path,
    gateway_ai_id: str = "",
) -> dict[str, str] | None:
    try:
        ai_id = await _resolve_ai_id_for_workspace(
            gateway_url,
            workspace=workspace,
            gateway_ai_id=gateway_ai_id,
        )
        if not ai_id:
            return None

        ais = await _fetch_gateway_json(f"{gateway_url}/ais")
        if not isinstance(ais, list):
            return None
        for item in ais:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() != ai_id:
                continue
            ai_socket = str(item.get("socket", "")).strip()
            if not ai_socket:
                return None
            return {
                "ai_id": ai_id,
                "ai_socket": ai_socket,
                "provider": str(item.get("provider", "")).strip() or "openai",
                "model": str(item.get("model", "")).strip(),
            }
    except Exception:
        return None
    return None


async def _fetch_spawn_config(gateway_url: str, ai_id: str) -> dict[str, str] | None:
    try:
        data = await _fetch_gateway_json(f"{gateway_url}/ais/{ai_id}/spawn-config")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {key: str(data.get(key, "")).strip() for key in ("provider", "model", "api_key", "base_url")}


def resolve_workspace(raw: str) -> Path:
    if raw.strip():
        return Path(raw.strip()).resolve()
    env = os.environ.get("WORKSPACE_DIR", "").strip()
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def resolve_project_root(workspace: Path) -> Path:
    for candidate in (workspace, *workspace.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return workspace


def psi_executable(repo_root: Path) -> str:
    custom = os.environ.get("PSI_CMD", "").strip()
    if custom:
        return shlex.split(custom)[0]
    if sys.platform == "win32":
        exe = repo_root / ".venv" / "Scripts" / "psi-agent.exe"
    else:
        exe = repo_root / ".venv" / "bin" / "psi-agent"
    if exe.is_file():
        return str(exe)
    return "psi-agent"


def _credentials_from_env() -> dict[str, str]:
    provider = (os.environ.get("PSI_AI_PROVIDER") or os.environ.get("FLOW_PSI_AI") or "openai").strip()
    model = (os.environ.get("PSI_AI_MODEL") or os.environ.get("FLOW_PSI_MODEL") or "").strip()
    api_key = (
        os.environ.get("PSI_AI_API_KEY") or os.environ.get("FLOW_PSI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    ).strip()
    base_url = (
        os.environ.get("PSI_AI_BASE_URL")
        or os.environ.get("FLOW_PSI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ""
    ).strip()
    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
    }


def _merge_credentials(
    base: dict[str, str],
    override: dict[str, str],
) -> dict[str, str]:
    merged = dict(base)
    for key in ("provider", "model", "api_key", "base_url"):
        value = override.get(key, "").strip()
        if value:
            merged[key] = value
    return merged


def _credentials_complete(creds: dict[str, str]) -> bool:
    if not creds["model"]:
        return False
    return bool(creds["api_key"] or creds["base_url"])


async def resolve_credentials(
    *,
    workspace: Path | None = None,
    gateway_ai_id: str = "",
) -> dict[str, str]:
    """Resolve LLM credentials: Gateway spawn-config → background registry → env."""
    ws = workspace or resolve_workspace("")
    creds = dict.fromkeys(("provider", "model", "api_key", "base_url"), "")

    gateway_url = await resolve_gateway_url(ws)
    if gateway_url:
        try:
            ai_id = await _resolve_ai_id_for_workspace(
                gateway_url,
                workspace=ws,
                gateway_ai_id=gateway_ai_id,
            )
            if ai_id:
                spawn = await _fetch_spawn_config(gateway_url, ai_id)
                if spawn:
                    creds = _merge_credentials(creds, spawn)
        except Exception:
            pass
    if _credentials_complete(creds):
        return creds

    parent = resolve_process_parent_ai_binding(ws)
    ai_socket = parent["ai_socket"] if parent else ""
    registry_creds = await credentials_from_background_registry(ws, ai_socket=ai_socket)
    creds = _merge_credentials(creds, registry_creds)
    if _credentials_complete(creds):
        return creds

    return _merge_credentials(creds, _credentials_from_env())


def _socket_prefix(workspace: Path) -> str:
    digest = uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex[:12]
    return f"ht-sub-{digest}"


def _free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _pick_tcp_ports() -> tuple[int, int]:
    ai_port = _free_tcp_port()
    ch_port = _free_tcp_port()
    while ch_port == ai_port:
        ch_port = _free_tcp_port()
    return ai_port, ch_port


def _unix_channel_socket(workspace: Path, session_id: str) -> str:
    prefix = _socket_prefix(workspace)
    return f"/tmp/{prefix}/channels/{session_id}.sock"


def _unix_sockets(workspace: Path, session_id: str) -> tuple[str, str]:
    prefix = _socket_prefix(workspace)
    ai_socket = f"/tmp/{prefix}/ai/{session_id}.sock"
    channel_socket = f"/tmp/{prefix}/channels/{session_id}.sock"
    return ai_socket, channel_socket


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _build_ai_argv(
    psi: str,
    *,
    provider: str,
    ai_socket: str,
    model: str,
    api_key: str,
    base_url: str,
) -> list[str]:
    argv = [psi, "ai", "--provider", provider, "--session-socket", ai_socket]
    if model:
        argv += ["--model", model]
    if api_key:
        argv += ["--api-key", api_key]
    if base_url:
        argv += ["--base-url", base_url]
    return argv


def _build_session_argv(
    psi: str,
    *,
    workspace: Path,
    channel_socket: str,
    ai_socket: str,
    session_id: str,
) -> list[str]:
    return [
        psi,
        "session",
        "--workspace",
        str(workspace),
        "--channel-socket",
        channel_socket,
        "--ai-socket",
        ai_socket,
        "--session-id",
        session_id,
    ]


def _powershell_exec_command(argv: list[str]) -> str:
    exe = _powershell_quote(argv[0])
    args = " ".join(_powershell_quote(arg) for arg in argv[1:])
    return f"& {exe} {args}"


def _bash_exec_command(argv: list[str]) -> str:
    parts = " ".join(shlex.quote(arg) for arg in argv)
    return f"exec {parts}"


def _plan_ok_payload(
    *,
    sid: str,
    workspace: Path,
    repo_root: Path,
    psi: str,
    shell: str,
    ai_socket: str,
    channel_socket: str,
    ai_command: str,
    session_command: str,
    reuse_parent_ai: bool,
    provider: str,
    model: str,
    has_api_key: bool,
    has_base_url: bool,
    gateway_url: str = "",
    binding_source: str = "",
) -> dict[str, Any]:
    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "repo_root": str(repo_root),
        "psi": psi,
        "shell": shell,
        "transport": "tcp" if sys.platform == "win32" else "unix",
        "reuse_parent_ai": reuse_parent_ai,
        "ai_socket": ai_socket,
        "channel_socket": channel_socket,
        "ai_process_id": "" if reuse_parent_ai else f"{sid}-ai",
        "session_process_id": f"{sid}-session",
        "ai_command": ai_command,
        "session_command": session_command,
        "provider": provider,
        "model": model,
        "has_api_key": has_api_key,
        "has_base_url": has_base_url,
        "gateway_url": gateway_url,
        "binding_source": binding_source,
    }


def _plan_reuse_parent_session(
    *,
    sid: str,
    workspace: Path,
    repo_root: Path,
    psi: str,
    parent: dict[str, str],
    gateway_url: str,
    binding_source: str,
) -> dict[str, Any]:
    ai_socket = parent["ai_socket"]
    if sys.platform == "win32":
        channel_socket = f"http://127.0.0.1:{_free_tcp_port()}"
        shell = "powershell"
        session_argv = _build_session_argv(
            psi,
            workspace=workspace,
            channel_socket=channel_socket,
            ai_socket=ai_socket,
            session_id=sid,
        )
        session_command = _powershell_exec_command(session_argv)
    else:
        channel_socket = _unix_channel_socket(workspace, sid)
        shell = "bash"
        session_argv = _build_session_argv(
            psi,
            workspace=workspace,
            channel_socket=channel_socket,
            ai_socket=ai_socket,
            session_id=sid,
        )
        session_command = _bash_exec_command(session_argv)

    return _plan_ok_payload(
        sid=sid,
        workspace=workspace,
        repo_root=repo_root,
        psi=psi,
        shell=shell,
        ai_socket=ai_socket,
        channel_socket=channel_socket,
        ai_command="",
        session_command=session_command,
        reuse_parent_ai=True,
        provider=parent["provider"],
        model=parent["model"],
        has_api_key=True,
        has_base_url=True,
        gateway_url=gateway_url,
        binding_source=binding_source,
    )


async def plan_subagent(
    *,
    session_id: str = "",
    workspace_raw: str = "",
    gateway_ai_id: str = "",
) -> dict[str, Any]:
    workspace = resolve_workspace(workspace_raw)
    repo_root = resolve_project_root(workspace)
    sid = session_id.strip() or f"sub-{uuid.uuid4().hex[:8]}"
    psi = psi_executable(repo_root)

    gateway_url = await resolve_gateway_url(workspace)
    if gateway_url:
        parent = await resolve_parent_ai_binding(
            gateway_url,
            workspace=workspace,
            gateway_ai_id=gateway_ai_id,
        )
        if parent and parent["ai_socket"]:
            ready = await wait_socket(parent["ai_socket"], timeout_seconds=3.0)
            if ready.get("ok"):
                return _plan_reuse_parent_session(
                    sid=sid,
                    workspace=workspace,
                    repo_root=repo_root,
                    psi=psi,
                    parent=parent,
                    gateway_url=gateway_url,
                    binding_source="gateway",
                )

    process_parent = resolve_process_parent_ai_binding(workspace)
    if process_parent and process_parent["ai_socket"]:
        ready = await wait_socket(process_parent["ai_socket"], timeout_seconds=3.0)
        if ready.get("ok"):
            return _plan_reuse_parent_session(
                sid=sid,
                workspace=workspace,
                repo_root=repo_root,
                psi=psi,
                parent=process_parent,
                gateway_url="",
                binding_source="process",
            )

    creds = await resolve_credentials(workspace=workspace, gateway_ai_id=gateway_ai_id)
    if not _credentials_complete(creds):
        return {
            "ok": False,
            "message": (
                "Missing model/base_url/api_key for standalone subagent AI. "
                "Gateway: link a model and ensure Gateway is reachable. "
                "Three-terminal: start parent AI with model/key on the command line, "
                "or use background_start for the AI process."
            ),
            "session_id": sid,
            "provider": creds["provider"],
            "model": creds["model"],
            "has_api_key": bool(creds["api_key"]),
            "has_base_url": bool(creds["base_url"]),
            "gateway_url": gateway_url,
            "reuse_parent_ai": False,
            "binding_source": "",
        }

    if sys.platform == "win32":
        ai_port, ch_port = _pick_tcp_ports()
        ai_socket = f"http://127.0.0.1:{ai_port}"
        channel_socket = f"http://127.0.0.1:{ch_port}"
        shell = "powershell"
        ai_argv = _build_ai_argv(psi, ai_socket=ai_socket, **creds)
        session_argv = _build_session_argv(
            psi,
            workspace=workspace,
            channel_socket=channel_socket,
            ai_socket=ai_socket,
            session_id=sid,
        )
        ai_command = _powershell_exec_command(ai_argv)
        session_command = _powershell_exec_command(session_argv)
    else:
        ai_socket, channel_socket = _unix_sockets(workspace, sid)
        shell = "bash"
        ai_argv = _build_ai_argv(psi, ai_socket=ai_socket, **creds)
        session_argv = _build_session_argv(
            psi,
            workspace=workspace,
            channel_socket=channel_socket,
            ai_socket=ai_socket,
            session_id=sid,
        )
        ai_command = _bash_exec_command(ai_argv)
        session_command = _bash_exec_command(session_argv)

    return _plan_ok_payload(
        sid=sid,
        workspace=workspace,
        repo_root=repo_root,
        psi=psi,
        shell=shell,
        ai_socket=ai_socket,
        channel_socket=channel_socket,
        ai_command=ai_command,
        session_command=session_command,
        reuse_parent_ai=False,
        provider=creds["provider"],
        model=creds["model"],
        has_api_key=bool(creds["api_key"]),
        has_base_url=bool(creds["base_url"]),
        gateway_url=gateway_url,
        binding_source="standalone",
    )


async def wait_socket(addr: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    addr = addr.strip()
    if not addr:
        return {"ok": False, "message": "socket address must not be empty"}
    connector, endpoint = resolve_connector_and_endpoint(addr)
    base = endpoint.rsplit("/chat/completions", 1)[0] or "http://localhost"
    deadline = anyio.current_time() + timeout_seconds
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            while anyio.current_time() < deadline:
                try:
                    async with session.get(base) as _resp:
                        pass
                    await anyio.sleep(0.3)
                    return {"ok": True, "socket": addr, "message": "ready"}
                except Exception:
                    await anyio.sleep(0.1)
    finally:
        await connector.close()
    return {
        "ok": False,
        "socket": addr,
        "message": f"socket not ready within {timeout_seconds}s",
    }


async def chat_subagent(
    *,
    channel_socket: str,
    message: str,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    message = message.strip()
    channel_socket = channel_socket.strip()
    if not channel_socket:
        return {"ok": False, "message": "channel_socket must not be empty", "text": ""}
    if not message:
        return {"ok": False, "message": "message must not be empty", "text": ""}

    text_parts: list[str] = []
    errors: list[str] = []
    try:
        with anyio.fail_after(timeout_seconds):
            async with ChannelCore(session_socket=channel_socket, interval=0.0) as core:
                async with aclosing(core.post([TextChunk(message)])) as stream:
                    async for chunk in stream:
                        if isinstance(chunk, TextChunk):
                            text_parts.append(chunk.text)
    except TimeoutError:
        return {
            "ok": False,
            "message": f"timed out after {timeout_seconds}s",
            "text": "".join(text_parts),
            "errors": errors,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": str(exc),
            "text": "".join(text_parts),
            "errors": errors,
        }

    text = "".join(text_parts)
    return {
        "ok": bool(text.strip()),
        "message": "ok" if text.strip() else "empty response from child",
        "text": text,
        "errors": errors,
    }


def dumps_result(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)
