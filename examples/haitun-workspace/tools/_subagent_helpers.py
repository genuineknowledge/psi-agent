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
    provider = (
        os.environ.get("PSI_AI_PROVIDER")
        or os.environ.get("FLOW_PSI_AI")
        or "openai"
    ).strip()
    model = (os.environ.get("PSI_AI_MODEL") or os.environ.get("FLOW_PSI_MODEL") or "").strip()
    api_key = (
        os.environ.get("PSI_AI_API_KEY")
        or os.environ.get("FLOW_PSI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
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


def _gateway_url() -> str:
    for key in ("PSI_GATEWAY_URL", "GATEWAY_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value.rstrip("/")
    return ""


async def _fetch_json(url: str, *, timeout_seconds: float = 3.0) -> Any:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.get(url) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def _resolve_ai_id_from_gateway(
    gateway_url: str,
    *,
    workspace: Path,
    gateway_ai_id: str = "",
) -> str:
    if gateway_ai_id.strip():
        return gateway_ai_id.strip()

    sessions = await _fetch_json(f"{gateway_url}/sessions")
    if isinstance(sessions, list):
        workspace_text = str(workspace)
        for item in sessions:
            if not isinstance(item, dict):
                continue
            ws = str(item.get("workspace", "")).strip()
            ai_id = str(item.get("ai_id", "")).strip()
            if ai_id and ws and ws == workspace_text:
                return ai_id

    ais = await _fetch_json(f"{gateway_url}/ais")
    if isinstance(ais, list) and len(ais) == 1:
        only = ais[0]
        if isinstance(only, dict):
            return str(only.get("id", "")).strip()
    return ""


async def resolve_credentials(
    *,
    workspace: Path | None = None,
    gateway_ai_id: str = "",
) -> dict[str, str]:
    creds = _credentials_from_env()
    if _credentials_complete(creds):
        return creds

    gateway_url = _gateway_url()
    if not gateway_url:
        return creds

    try:
        ai_id = await _resolve_ai_id_from_gateway(
            gateway_url,
            workspace=workspace or resolve_workspace(""),
            gateway_ai_id=gateway_ai_id,
        )
        if not ai_id:
            ais = await _fetch_json(f"{gateway_url}/ais")
            if isinstance(ais, list):
                for item in ais:
                    if isinstance(item, dict):
                        model = str(item.get("model", "")).strip()
                        ai_id = str(item.get("id", "")).strip()
                        if ai_id and model:
                            break
        if not ai_id:
            return creds

        spawn = await _fetch_json(f"{gateway_url}/ais/{ai_id}/spawn-config")
        if isinstance(spawn, dict):
            creds = _merge_credentials(creds, {k: str(spawn.get(k, "")) for k in creds})
    except Exception:
        return creds
    return creds


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


async def plan_subagent(
    *,
    session_id: str = "",
    workspace_raw: str = "",
    gateway_ai_id: str = "",
) -> dict[str, Any]:
    workspace = resolve_workspace(workspace_raw)
    repo_root = resolve_project_root(workspace)
    sid = session_id.strip() or f"sub-{uuid.uuid4().hex[:8]}"
    creds = await resolve_credentials(workspace=workspace, gateway_ai_id=gateway_ai_id)
    psi = psi_executable(repo_root)

    if not _credentials_complete(creds):
        return {
            "ok": False,
            "message": (
                "Missing model/base_url/api_key for subagent AI. "
                "Link a model in Gateway UI (POST /ais) or set "
                "FLOW_PSI_MODEL + FLOW_PSI_BASE_URL + OPENAI_API_KEY in env."
            ),
            "session_id": sid,
            "provider": creds["provider"],
            "model": creds["model"],
            "has_api_key": bool(creds["api_key"]),
            "has_base_url": bool(creds["base_url"]),
            "gateway_url": _gateway_url(),
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

    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "repo_root": str(repo_root),
        "psi": psi,
        "shell": shell,
        "transport": "tcp" if sys.platform == "win32" else "unix",
        "ai_socket": ai_socket,
        "channel_socket": channel_socket,
        "ai_process_id": f"{sid}-ai",
        "session_process_id": f"{sid}-session",
        "ai_command": ai_command,
        "session_command": session_command,
        "provider": creds["provider"],
        "model": creds["model"],
        "has_api_key": bool(creds["api_key"]),
        "has_base_url": bool(creds["base_url"]),
    }


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
