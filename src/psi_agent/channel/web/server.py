from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import anyio
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError
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


AGENT_ROUTES_KEY = web.AppKey("agent_routes", AgentRoutes)
UPLOAD_ROOT_KEY = web.AppKey("upload_root", Path)
DOWNLOAD_ROOTS_KEY = web.AppKey("download_roots", list[Path])
FRONTEND_DIST_KEY = web.AppKey("frontend_dist", Path)
DEFAULT_UPLOAD_DIR = "~/.psi-agent/channel-web/uploads"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def handle_index(request: web.Request) -> web.StreamResponse:
    dist = request.app.get(FRONTEND_DIST_KEY)
    if dist is not None:
        index = dist / "index.html"
        if index.is_file():
            return web.FileResponse(index)
    return web.Response(text=INDEX_HTML, content_type="text/html")


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
    *, routes: AgentRoutes, listen: str, upload_dir: str = "", frontend_dist: str = ""
) -> None:
    app = web.Application()
    app[AGENT_ROUTES_KEY] = routes
    app[UPLOAD_ROOT_KEY] = _resolve_upload_root(upload_dir)
    app[DOWNLOAD_ROOTS_KEY] = _resolve_download_roots(app[UPLOAD_ROOT_KEY])
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/upload", handle_upload)
    app.router.add_get("/api/download", handle_download)

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
