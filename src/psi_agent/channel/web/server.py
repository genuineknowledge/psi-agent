from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import signal
import socket
import subprocess
import time
import uuid
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import anyio
from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.client_exceptions import ClientConnectionResetError, ClientError
from loguru import logger

from psi_agent.channel.session_client import stream_session_reply
from psi_agent.errors import UserFacingError
from psi_agent.net import make_server_site

from .page import INDEX_HTML


@dataclass(frozen=True)
class AgentRoutes:
    """Maps the UI's (flow, security) switches to four agent session endpoints."""

    default: str
    fusion_offguard: str  # flow=on,  security=off
    fusion_onguard: str   # flow=on,  security=on
    hermes_offguard: str  # flow=off, security=off
    hermes_onguard: str   # flow=off, security=on

    def select(self, *, flow: bool, security: bool) -> str:
        if flow:
            return self.fusion_onguard if security else self.fusion_offguard
        return self.hermes_onguard if security else self.hermes_offguard

    def label(self, *, flow: bool, security: bool) -> str:
        family = "fusion" if flow else "hermes"
        guard = "onguard" if security else "offguard"
        return f"{family}-{guard}"


AGENT_ROUTES_KEY = web.AppKey("agent_routes", AgentRoutes)
UPLOAD_ROOT_KEY = web.AppKey("upload_root", Path)
DOWNLOAD_ROOTS_KEY = web.AppKey("download_roots", list[Path])
FRONTEND_DIST_KEY = web.AppKey("frontend_dist", Path)
DEMO_TARGET_KEY = web.AppKey("demo_target", str)
DEMO_CLIENT_KEY = web.AppKey("demo_client", ClientSession)
DEFAULT_UPLOAD_DIR = "~/.psi-agent/channel-web/uploads"
DEFAULT_DEMO_TARGET = "http://127.0.0.1:8099"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FUSION_GUARD_ROOT = "/home/ecs-user/Fusion-Guard"
OPENCLAW_TE_DAEMON_SOCKET = "/run/openclaw-security/te-daemon.sock"
OPENCLAW_TE_DAEMON_TOKEN_FILE = "/home/ecs-user/.openclaw/security/te-daemon.token"


async def handle_index(request: web.Request) -> web.StreamResponse:
    dist = request.app.get(FRONTEND_DIST_KEY)
    if dist is not None:
        index = dist / "index.html"
        if index.is_file():
            return web.FileResponse(index)
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def handle_demo_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/demo/")


async def handle_demo_proxy(request: web.Request) -> web.StreamResponse:
    """Reverse-proxy the embedded demo module mounted under ``/demo/``.

    The demo is a standalone HTTP service (default ``127.0.0.1:8099``) whose HTML
    calls absolute paths like ``/api/state``. We serve it under ``/demo/`` and
    rewrite those calls to ``/demo/api/...`` so the iframe stays self-contained
    and never collides with the web channel's own ``/api/*`` routes.
    """
    target = request.app[DEMO_TARGET_KEY]
    client = request.app[DEMO_CLIENT_KEY]
    tail = request.match_info.get("tail", "")
    url = f"{target}/{tail}"
    if request.query_string:
        url = f"{url}?{request.query_string}"

    body = await request.read()
    hop_by_hop = {"host", "connection", "keep-alive", "transfer-encoding", "content-length"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}

    try:
        async with client.request(
            request.method, url, data=body or None, headers=fwd_headers, allow_redirects=False
        ) as upstream:
            raw = await upstream.read()
            content_type = upstream.headers.get("Content-Type", "application/octet-stream")
            # Rewrite absolute /api/ calls in the demo's HTML/JS to /demo/api/.
            if "text/html" in content_type or "javascript" in content_type:
                raw = raw.replace(b'"/api/', b'"/demo/api/').replace(b"'/api/", b"'/demo/api/")
            resp = web.Response(body=raw, status=upstream.status)
            resp.headers["Content-Type"] = content_type
            return resp
    except (ClientError, ConnectionError) as e:
        logger.warning(f"Demo proxy to {url} failed: {e}")
        return web.Response(text=f"demo module unavailable: cannot reach {target}", status=502)


async def handle_upload(request: web.Request) -> web.StreamResponse:
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "missing file"}, status=400)

    original_name = _sanitize_file_name(unquote(field.filename or "upload.bin"))
    upload_root = request.app[UPLOAD_ROOT_KEY]
    upload_id = uuid.uuid4().hex
    upload_dir = upload_root / upload_id
    path = upload_dir / original_name

    data = await field.read(decode=False)
    if len(data) > _max_upload_bytes():
        return web.json_response({"error": "file too large"}, status=413)

    await anyio.Path(upload_dir).mkdir(parents=True, exist_ok=True)
    await anyio.Path(path).write_bytes(data)
    return web.json_response(
        {
            "id": upload_id,
            "name": original_name,
            "path": str(path),
            "size": len(data),
            "content_type": field.headers.get("Content-Type", "application/octet-stream"),
        }
    )


async def handle_download(request: web.Request) -> web.StreamResponse:
    raw_path = str(request.query.get("path", ""))
    if not raw_path:
        return web.json_response({"error": "missing path"}, status=400)

    try:
        path = _resolve_download_path(raw_path, request.app[DOWNLOAD_ROOTS_KEY])
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=403)

    return web.FileResponse(
        path,
        headers={"Content-Disposition": _content_disposition(path.name)},
        chunk_size=256 * 1024,
    )


async def handle_chat(request: web.Request) -> web.StreamResponse:
    """Proxy one user message to the routed agent and stream the reply as SSE.

    The browser sends ``{message, modules, compare}``. We route to one of four
    agents by ``modules.flow`` / ``modules.security`` (see ``AgentRoutes``), then
    relay ``content`` (and ``reasoning``) deltas back.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON"}, status=400)

    message = str(body.get("message", "")).strip()
    attachments = _parse_attachments(body.get("attachments"), request.app[UPLOAD_ROOT_KEY])
    if not message and not attachments:
        return web.json_response({"error": "empty message"}, status=400)
    message = _compose_message_with_attachments(message, attachments)

    flow, security = _parse_route_flags(body)
    routes = request.app[AGENT_ROUTES_KEY]
    session_socket = routes.select(flow=flow, security=security)
    logger.info(f"Web chat routed to flow={flow} security={security} -> {session_socket}")

    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    await resp.prepare(request)

    try:
        async for delta in stream_session_reply(session_socket=session_socket, message=message):
            payload: dict[str, str] = {}
            if delta.reasoning:
                payload["reasoning"] = delta.reasoning
            if delta.content:
                payload["content"] = delta.content
            if payload:
                if not await _write_sse_payload(resp, payload):
                    return resp
    except UserFacingError as e:
        if not await _write_sse_payload(resp, {"error": str(e)}):
            return resp
    except Exception as e:
        logger.exception("Web channel chat error")
        if not await _write_sse_payload(resp, {"error": f"Unexpected error: {e}"}):
            return resp

    await _write_sse_done(resp)
    return resp


async def handle_restart_session(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON"}, status=400)

    flow, security = _parse_route_flags(body)
    routes = request.app[AGENT_ROUTES_KEY]
    session_socket = routes.select(flow=flow, security=security)
    route_label = routes.label(flow=flow, security=security)
    try:
        result = await anyio.to_thread.run_sync(_restart_local_session, session_socket, route_label)
    except RestartSessionError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    except Exception as exc:
        logger.exception("Failed to restart web-routed session")
        return web.json_response({"error": f"unexpected restart error: {exc}"}, status=500)
    return web.json_response(result)


def _parse_route_flags(body: dict[str, Any]) -> tuple[bool, bool]:
    """Derive (flow, security) from the request body.

    `compare=true` (Hermes mode) forces flow off so it lands on a hermes-* agent.
    """
    modules = body.get("modules")
    modules = modules if isinstance(modules, dict) else {}
    flow = bool(modules.get("flow", True))
    security = bool(modules.get("security", True))
    if bool(body.get("compare", False)):
        flow = False
    return flow, security


async def serve_web_channel(
    *, routes: AgentRoutes, listen: str, upload_dir: str = "", frontend_dist: str = "", demo_target: str = ""
) -> None:
    app = web.Application()
    app[AGENT_ROUTES_KEY] = routes
    app[UPLOAD_ROOT_KEY] = _resolve_upload_root(upload_dir)
    app[DOWNLOAD_ROOTS_KEY] = _resolve_download_roots(app[UPLOAD_ROOT_KEY])
    app[DEMO_TARGET_KEY] = demo_target or os.environ.get("PSI_WEB_DEMO_TARGET", "") or DEFAULT_DEMO_TARGET
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/restart-session", handle_restart_session)
    app.router.add_post("/api/upload", handle_upload)
    app.router.add_get("/api/download", handle_download)
    app.router.add_route("*", "/demo", handle_demo_redirect)
    app.router.add_route("*", "/demo/{tail:.*}", handle_demo_proxy)
    app.router.add_get("/", handle_index)

    async def _open_demo_client(app: web.Application) -> None:
        app[DEMO_CLIENT_KEY] = ClientSession(timeout=ClientTimeout(total=60))

    async def _close_demo_client(app: web.Application) -> None:
        await app[DEMO_CLIENT_KEY].close()

    app.on_startup.append(_open_demo_client)
    app.on_cleanup.append(_close_demo_client)

    dist = _resolve_frontend_dist(frontend_dist)
    if dist is not None:
        app[FRONTEND_DIST_KEY] = dist
        app.router.add_static("/assets", dist / "assets")
        logger.info(f"Serving built frontend from {dist}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = await make_server_site(runner, listen)
    await site.start()

    logger.info(f"Web channel listening on {listen}")
    logger.info(f"Demo module proxied at /demo/ -> {app[DEMO_TARGET_KEY]}")
    logger.warning("Web channel has no authentication. Bind to 127.0.0.1 or put it behind a trusted proxy.")

    try:
        await anyio.sleep_forever()
    finally:
        await runner.cleanup()


def _resolve_upload_root(raw: str = "") -> Path:
    configured = raw or os.environ.get("PSI_WEB_UPLOAD_DIR", "") or DEFAULT_UPLOAD_DIR
    return Path(configured).expanduser().resolve()


def _resolve_frontend_dist(raw: str = "") -> Path | None:
    """Locate the built Vue frontend (dist/). Defaults to ./frontend/dist next to this module."""
    configured = raw or os.environ.get("PSI_WEB_FRONTEND_DIST", "")
    candidate = Path(configured).expanduser() if configured else Path(__file__).parent / "frontend" / "dist"
    candidate = candidate.resolve()
    return candidate if (candidate / "index.html").is_file() else None


class RestartSessionError(RuntimeError):
    pass


def _restart_local_session(session_socket: str, route_label: str) -> dict[str, Any]:
    static_launch = _static_root_session_launch(route_label, session_socket)
    matches = _find_session_processes(session_socket)
    if static_launch is None and not matches:
        raise RestartSessionError(f"no running session process found for {session_socket}")

    if static_launch is not None:
        cmdline = static_launch["cmdline"]
        cwd = static_launch["cwd"]
        disabled_tools = static_launch["disabled_tools"]
        force_root = True
    else:
        parent = next((item for item in matches if _is_uv_session_command(item["cmdline"])), matches[0])
        cmdline = list(parent["cmdline"])
        cwd = str(parent["cwd"])
        disabled_tools = ""
        force_root = False

    pids = sorted({int(item["pid"]) for item in matches}, reverse=True)
    if pids:
        logger.info(f"Restarting web session {route_label} at {session_socket}; stopping pids={pids}")
        _terminate_pids(pids)
    else:
        logger.info(f"Starting missing web session {route_label} at {session_socket}")

    if route_label == "hermes-onguard":
        _reset_hermes_onguard_security_state()

    socket_path = Path(session_socket)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"Could not remove stale session socket {session_socket}: {exc}")

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if disabled_tools:
        env["PSI_AGENT_DISABLED_TOOLS"] = disabled_tools
    else:
        env.pop("PSI_AGENT_DISABLED_TOOLS", None)

    log_path = Path(cwd) / "logs" / f"session-{route_label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        launch_cmd = _root_launch_command(cmdline, cwd, disabled_tools) if force_root else cmdline
        process = subprocess.Popen(
            launch_cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_file.close()

    _wait_for_socket(session_socket)
    logger.info(f"Restarted web session {route_label} at {session_socket}; pid={process.pid}")
    return {"ok": True, "route": route_label, "session_socket": session_socket, "pid": process.pid}


def _static_root_session_launch(route_label: str, session_socket: str) -> dict[str, Any] | None:
    workspace_by_route = {
        "hermes-offguard": "/home/ecs-user/Dolphin-Agent/examples/hermes-offguard",
        "hermes-onguard": "/home/ecs-user/Dolphin-Agent/examples/hermes-onguard",
    }
    workspace = workspace_by_route.get(route_label)
    if workspace is None:
        return None
    return {
        "cwd": "/home/ecs-user/Dolphin-Agent",
        "disabled_tools": "edit,write" if route_label == "hermes-onguard" else "",
        "cmdline": [
            "uv",
            "run",
            "--no-sync",
            "psi-agent",
            "session",
            "--workspace",
            workspace,
            "--channel-socket",
            session_socket,
            "--ai-socket",
            "/home/ecs-user/.psi-agent/run/dolphin-ai.sock",
            "--model",
            "deepseek-v4-pro",
        ],
    }


def _find_session_processes(session_socket: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    proc = Path("/proc")
    if not proc.is_dir():
        raise RestartSessionError("/proc is not available; cannot locate session process")

    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        pid = int(item.name)
        try:
            raw_cmdline = (item / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw_cmdline:
            continue
        cmdline = [part.decode(errors="replace") for part in raw_cmdline.split(b"\0") if part]
        if not _is_session_command_for_socket(cmdline, session_socket):
            continue
        try:
            cwd = (item / "cwd").resolve()
        except OSError:
            cwd = Path.cwd()
        matches.append({"pid": pid, "cmdline": cmdline, "cwd": cwd})
    return matches


def _is_session_command_for_socket(cmdline: list[str], session_socket: str) -> bool:
    joined = " ".join(cmdline)
    return (
        "psi-agent" in joined
        and "session" in cmdline
        and "--channel-socket" in cmdline
        and session_socket in cmdline
    )


def _is_uv_session_command(cmdline: list[str]) -> bool:
    exe = Path(cmdline[0]).name if cmdline else ""
    return exe == "uv" and "run" in cmdline and "psi-agent" in cmdline and "session" in cmdline


def _root_launch_command(cmdline: list[str], cwd: str, disabled_tools: str) -> list[str]:
    exports = ["PYTHONUNBUFFERED=1"]
    if disabled_tools:
        exports.append(f"PSI_AGENT_DISABLED_TOOLS={shlex.quote(disabled_tools)}")
    exports.extend(
        [
            f"FUSION_GUARD_ROOT={shlex.quote(FUSION_GUARD_ROOT)}",
            "OPENCLAW_SECURITY_HOST_RUNTIME=dolphin",
            f"OPENCLAW_TE_DAEMON_SOCKET={shlex.quote(OPENCLAW_TE_DAEMON_SOCKET)}",
            f"OPENCLAW_TE_DAEMON_TOKEN_FILE={shlex.quote(OPENCLAW_TE_DAEMON_TOKEN_FILE)}",
        ]
    )
    script = (
        f"cd {shlex.quote(cwd)} && "
        "umask 000 && "
        f"export {' '.join(exports)}; "
        f"exec {shlex.join(cmdline)}"
    )
    return ["sudo", "-n", "bash", "-lc", script]


def _reset_hermes_onguard_security_state() -> None:
    workspace = Path("/home/ecs-user/Dolphin-Agent/examples/hermes-onguard")
    module_name = _dolphin_security_module_name(workspace)
    logger.info(f"Resetting hermes-onguard security state; module={module_name}")
    try:
        result = _openclaw_daemon_post("/policy/remove", {"moduleName": module_name})
        logger.info(f"Removed hermes-onguard security policy: {result}")
    except Exception as exc:
        logger.warning(f"Could not remove hermes-onguard security policy {module_name}: {exc}")

    for path in (workspace / ".openclaw", workspace / ".openclaw-security-artifacts"):
        _reset_workspace_state_dir(path)


def _dolphin_security_module_name(workspace: Path) -> str:
    workspace_dir = str(workspace.resolve())
    session_key = "dolphin:" + hashlib.sha256(workspace_dir.encode("utf-8")).hexdigest()[:16]
    scope_key = f"main\nskey:{session_key}"
    session_slug = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:16]
    return f"fusionclaw_main_{session_slug}"


def _reset_workspace_state_dir(path: Path) -> None:
    if path.exists() and path.is_dir():
        for child in path.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    elif path.exists():
        path.unlink(missing_ok=True)
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)


def _openclaw_daemon_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = Path(OPENCLAW_TE_DAEMON_TOKEN_FILE).expanduser().read_text("utf-8").strip()
    body = json.dumps(payload).encode("utf-8")
    request_lines = [
        f"POST {path} HTTP/1.1",
        "Host: localhost",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
        f"Authorization: Bearer {token}",
        "Connection: close",
        "",
        "",
    ]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect(OPENCLAW_TE_DAEMON_SOCKET)
        sock.sendall("\r\n".join(request_lines).encode("utf-8") + body)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()

    raw = b"".join(chunks)
    if not raw:
        raise RuntimeError(f"empty response from daemon endpoint {path}")
    header_blob, _, body_blob = raw.partition(b"\r\n\r\n")
    status_line = header_blob.splitlines()[0].decode("utf-8", "replace")
    try:
        _version, status_code_text, _rest = status_line.split(" ", 2)
        status_code = int(status_code_text)
    except ValueError as exc:
        raise RuntimeError(f"invalid daemon response status line: {status_line}") from exc
    parsed = json.loads(body_blob.decode("utf-8") or "{}")
    if status_code >= 400:
        raise RuntimeError(parsed.get("error") or parsed.get("reason") or f"daemon request failed: {status_code}")
    if not isinstance(parsed, dict):
        raise RuntimeError("unexpected daemon response payload")
    return parsed


def _terminate_pids(pids: list[int]) -> None:
    own_pid = os.getpid()
    for sig, delay in ((signal.SIGTERM, 1.5), (signal.SIGKILL, 0.0)):
        remaining: list[int] = []
        for pid in pids:
            if pid == own_pid:
                continue
            try:
                _kill_pid(pid, sig)
            except ProcessLookupError:
                continue
            except OSError as exc:
                logger.warning(f"Failed to send {sig.name} to pid {pid}: {exc}")
        if delay:
            time.sleep(delay)
        for pid in pids:
            if pid != own_pid and _pid_exists(pid):
                remaining.append(pid)
        if not remaining:
            return
        pids = remaining


def _kill_pid(pid: int, sig: signal.Signals) -> None:
    uid = _pid_uid(pid)
    if uid is not None and uid != os.geteuid() and os.geteuid() != 0:
        subprocess.run(["sudo", "-n", "kill", f"-{sig.name}", str(pid)], check=False)
        return
    os.kill(pid, sig)


def _pid_uid(pid: int) -> int | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^Uid:\s+(\d+)", status, re.MULTILINE)
    return int(match.group(1)) if match else None


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_socket(session_socket: str) -> None:
    deadline = time.monotonic() + 15
    path = Path(session_socket)
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.2)
    raise RestartSessionError(f"session did not recreate socket within timeout: {session_socket}")


def _max_upload_bytes() -> int:
    raw = os.environ.get("PSI_WEB_UPLOAD_MAX_BYTES", "")
    if not raw:
        return MAX_UPLOAD_BYTES
    try:
        parsed = int(raw)
    except ValueError:
        return MAX_UPLOAD_BYTES
    return parsed if parsed > 0 else MAX_UPLOAD_BYTES


def _sanitize_file_name(name: str) -> str:
    cleaned = Path(name.strip().replace("\\", "/")).name
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "_", cleaned)
    cleaned = re.sub(r'[<>:"|?*]+', "_", cleaned).strip(" .")
    return cleaned or "upload.bin"


def _resolve_download_roots(upload_root: Path) -> list[Path]:
    raw = os.environ.get("PSI_WEB_DOWNLOAD_ROOTS", "")
    roots = [upload_root, Path.cwd().resolve()]
    if raw:
        roots.extend(Path(item).expanduser().resolve() for item in raw.split(os.pathsep) if item.strip())
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            unique.append(root)
            seen.add(root)
    return unique


def _resolve_download_path(raw_path: str, roots: list[Path]) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("download file does not exist")
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return path
    raise ValueError("download path is outside allowed directories")


def _content_disposition(name: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


def _parse_attachments(value: Any, upload_root: Path) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    attachments: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path", ""))
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        try:
            path.relative_to(upload_root)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="attachment path is outside upload directory") from exc
        if not path.is_file():
            raise web.HTTPBadRequest(text="attachment file does not exist")
        attachments.append({"name": _sanitize_file_name(str(item.get("name") or path.name)), "path": str(path)})
    return attachments


def _compose_message_with_attachments(message: str, attachments: list[dict[str, str]]) -> str:
    if not attachments:
        return message
    lines = [message] if message else []
    lines.append("用户上传了附件：")
    for item in attachments:
        lines.append(f"- {item['name']}")
        lines.append(f"FILE:{item['path']}")
    return "\n".join(lines)


async def _write_sse_payload(resp: web.StreamResponse, payload: dict[str, str]) -> bool:
    return await _write_sse_bytes(resp, f"data: {json.dumps(payload)}\n\n".encode())


async def _write_sse_done(resp: web.StreamResponse) -> bool:
    return await _write_sse_bytes(resp, b"data: [DONE]\n\n")


async def _write_sse_bytes(resp: web.StreamResponse, data: bytes) -> bool:
    try:
        await resp.write(data)
    except (ClientConnectionResetError, ConnectionResetError):
        logger.debug("Web channel client disconnected while streaming SSE")
        return False
    return True
