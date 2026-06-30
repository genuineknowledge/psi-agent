from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._chat_manager import ChatManager
from psi_agent.gateway._history_manager import HistoryManager
from psi_agent.gateway._manager import (
    AiCreateRequest,
    SessionCreateRequest,
)
from psi_agent.gateway._openapi import render_openapi
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._title_manager import TitleManager
from psi_agent.gateway._workspace_manager import WorkspaceManager


async def _handle_spa(request: web.Request) -> web.HTTPFound:
    raise web.HTTPFound("/spa/index.html")


async def _handle_openapi(request: web.Request) -> web.Response:
    return web.Response(text=render_openapi(), content_type="application/json")


async def _handle_favicon(request: web.Request) -> web.FileResponse:
    favicon_path: str = request.app["favicon_path"]
    logger.debug(f"Serving favicon from {favicon_path}")
    return web.FileResponse(favicon_path)


def _json(data: object, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
    )


def _error(message: str, status: int) -> web.Response:
    return _json({"error": message}, status=status)


async def create_app(aim: AIManager, sm: SessionManager, favicon_path: str | None = None) -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app["aim"] = aim
    app["sm"] = sm
    app["tm"] = TitleManager()
    app["wm"] = WorkspaceManager()
    app["cm"] = ChatManager()
    app["hm"] = HistoryManager()
    app["favicon_path"] = favicon_path

    spa_dist = anyio.Path(__file__).parent / "spa" / "dist"
    if await spa_dist.exists():
        app.router.add_static("/spa/", str(spa_dist), show_index=False)
    app.router.add_get("/", _handle_spa)
    app.router.add_get("/spa", _handle_spa)
    app.router.add_get("/spa/", _handle_spa)
    if favicon_path is not None:
        logger.info(f"Favicon enabled, serving {favicon_path} at /favicon.ico")
        app.router.add_get("/favicon.ico", _handle_favicon)
    app.router.add_get("/openapi.json", _handle_openapi)
    app.router.add_post("/ais", _create_ai)
    app.router.add_delete("/ais/{ai_id}", _delete_ai)
    app.router.add_get("/ais", _list_ais)
    app.router.add_post("/sessions", _create_session)
    app.router.add_delete("/sessions/{session_id}", _delete_session)
    app.router.add_get("/sessions", _list_sessions)
    app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
    app.router.add_get("/sessions/{session_id}/history", _get_history)
    app.router.add_get("/titles", _list_titles)
    app.router.add_post("/titles", _set_title)
    app.router.add_post("/titles/generate", _generate_title)
    app.router.add_get("/workspace/browse", _browse_workspace)

    return app


async def _create_ai(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    try:
        body = await request.json()
        req = AiCreateRequest(**body)
        info = await aim.create(req)
        return _json(asdict(info), status=201)
    except (TypeError, ValueError) as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error creating AI: {e}")
        return _error(str(e), status=500)


async def _delete_ai(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    ai_id = request.match_info["ai_id"]
    try:
        info = await aim.delete(ai_id)
        return _json(asdict(info))
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error deleting AI '{ai_id}': {e}")
        return _error(str(e), status=500)


async def _list_ais(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    return _json([asdict(i) for i in await aim.list_all()])


async def _create_session(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    try:
        body = await request.json()
        req = SessionCreateRequest(**body)
        info = await sm.create(req)
        return _json(asdict(info), status=201)
    except (TypeError, ValueError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error creating session: {e}")
        return _error(str(e), status=500)


async def _delete_session(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    session_id = request.match_info["session_id"]
    try:
        info = await sm.delete(session_id)
        return _json(asdict(info))
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error deleting session '{session_id}': {e}")
        return _error(str(e), status=500)


async def _list_sessions(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    return _json([asdict(i) for i in await sm.list_all()])


async def _list_titles(request: web.Request) -> web.Response:
    tm: TitleManager = request.app["tm"]
    return _json(tm.get_all())


async def _set_title(request: web.Request) -> web.Response:
    tm: TitleManager = request.app["tm"]
    try:
        body = await request.json()
        sid = body["id"]
        tm.set(sid, body["title"])
        return _json({"id": sid, "title": body["title"]})
    except (KeyError, TypeError) as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error setting title: {e}")
        return _error(str(e), status=500)


async def _generate_title(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    sm: SessionManager = request.app["sm"]
    tm: TitleManager = request.app["tm"]
    try:
        body = await request.json()
        sid = body["id"]
        user_text = body.get("user_text", "")
        assistant_text = body.get("assistant_text", "")
    except (KeyError, TypeError) as e:
        return _error(str(e), status=400)

    try:
        sessions = await sm.list_all()
        sess = next((s for s in sessions if s.id == sid), None)
        if not sess:
            return _error("Session not found", status=404)
        ai_socket = aim.get_socket(sess.ai_id)
    except LookupError as e:
        return _error(str(e), status=404)

    title = await tm.generate(sid, ai_socket, user_text, assistant_text)
    if title:
        return _json({"id": sid, "title": title})
    logger.warning(f"Title generation returned no result for session {sid}")
    return _error("Failed to generate title", status=500)


async def _browse_workspace(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app["wm"]
    path = request.query.get("path") or str(Path.cwd())
    try:
        result = await wm.browse(path)
        parent = str(Path(path).parent)
        return _json({"path": path, "parent": parent, **result})
    except (OSError, PermissionError) as e:
        return _error(str(e), status=400)


async def _get_history(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    hm: HistoryManager = request.app["hm"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    messages = await hm.get(workspace, session_id)
    return _json(messages)


async def _handle_chat(request: web.Request) -> web.StreamResponse:
    sm: SessionManager = request.app["sm"]
    cm: ChatManager = request.app["cm"]
    session_id = request.match_info["session_id"]
    try:
        channel_socket = sm.get_channel_socket(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)

    if request.content_type and "multipart" in request.content_type:
        data = await request.post()
        raw = data.get("chunks")
        raw_chunks = json.loads(str(raw)) if raw else []
        file_field = data.get("file")
        body: dict[str, Any] = {"chunks": raw_chunks}
        if file_field is not None:
            fname = getattr(file_field, "filename", None)
            if fname:
                path = _downloads_path(fname)
                await anyio.Path(path).parent.mkdir(parents=True, exist_ok=True)
                content = await anyio.to_thread.run_sync(file_field.file.read)  # ty: ignore
                await anyio.Path(path).write_bytes(content)
                body["chunks"].append({"type": "file", "path": path})
    else:
        body = await request.json()

    resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)

    client_gone = False
    try:
        async for chunk in cm.handle(channel_socket, body):
            try:
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            except ConnectionResetError:
                # Client disconnected (e.g. pressed "stop") — normal, stop streaming.
                # cm.handle's close propagates cancellation to the upstream agent/LLM.
                client_gone = True
                logger.info(f"Client disconnected during chat for session '{session_id}'; cancelling stream")
                break
    except ConnectionResetError:
        client_gone = True
        logger.info(f"Client disconnected during chat for session '{session_id}'; cancelling stream")
    except Exception as e:
        logger.warning(f"Chat error for session {session_id!r}: {e!r}")
        if not client_gone:
            with contextlib.suppress(ConnectionResetError):
                await resp.write(f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n".encode())

    if not client_gone:
        with contextlib.suppress(ConnectionResetError):
            await resp.write(b"data: [DONE]\n\n")
    return resp


def _downloads_path(filename: str) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    base = os.path.join(str(Path.home()), "Downloads", ".psi", date)
    return os.path.join(base, os.path.basename(filename))
