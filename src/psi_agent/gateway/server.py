from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress
from dataclasses import asdict
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._attention import AttentionHub
from psi_agent.gateway._chat_manager import ChatManager
from psi_agent.gateway._feishu_manager import FeishuManager
from psi_agent.gateway._history_manager import HistoryManager
from psi_agent.gateway._openapi import render_openapi
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME, inject_app_name, read_spa_index_template
from psi_agent.gateway._title_manager import TitleManager
from psi_agent.gateway._todo_manager import TodoManager
from psi_agent.gateway._workspace_manager import WorkspaceManager

# Browser fetch often dies during multi-minute tool silence; SSE comments keep it open.
_SSE_KEEPALIVE_SEC = 15.0


async def _write_chat_sse_with_keepalive(
    resp: web.StreamResponse,
    chunks: AsyncGenerator[dict[str, Any]],
    *,
    session_id: str,
    keepalive_sec: float = _SSE_KEEPALIVE_SEC,
) -> None:
    """Write chat SSE chunks, emitting comment keepalives on idle.

    Keepalives must **not** wrap ``agen.__anext__()`` in ``anyio.fail_after``.
    Cancelling ``__anext__`` tears down ChatManager / ChannelCore, so the browser
    gets early ``[DONE]`` while Session is still waiting on the model — SPA then
    spins forever on「正在同步」and the assistant reply is never committed.
    """
    send, recv = anyio.create_memory_object_stream[dict[str, Any]](64)

    async def pump() -> None:
        async with send, aclosing(chunks) as stream:
            async for chunk in stream:
                await send.send(chunk)

    async with anyio.create_task_group() as tg:
        tg.start_soon(pump)
        async with recv:
            while True:
                try:
                    with anyio.fail_after(keepalive_sec):
                        chunk = await recv.receive()
                except TimeoutError:
                    with suppress(Exception):
                        await resp.write(b": keepalive\n\n")
                        logger.debug(f"Chat SSE keepalive for session {session_id!r}")
                    continue
                except anyio.EndOfStream:
                    break
                data = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await resp.write(data.encode())
                logger.debug(f"Chat SSE chunk: {data[:1000]}")


async def _handle_spa(request: web.Request) -> web.HTTPFound:
    raise web.HTTPFound("/spa/index.html")


async def _handle_spa_v2(request: web.Request) -> web.HTTPFound:
    raise web.HTTPFound("/spa-v2/index.html")


async def _handle_openapi(request: web.Request) -> web.Response:
    return web.Response(text=render_openapi(), content_type="application/json")


async def _handle_spa_index(request: web.Request) -> web.Response:
    app_name: str = request.app["app_name"]
    template = await read_spa_index_template()
    if template is None:
        return _error("SPA index.html not found", status=404)
    body = inject_app_name(template, app_name)
    return web.Response(text=body, content_type="text/html", charset="utf-8")


async def _handle_spa_v2_index(request: web.Request) -> web.Response:
    app_name: str = request.app["app_name"]
    base = anyio.Path(__file__).parent / "spa-v2"
    template: str | None = None
    for rel in ("dist/index.html", "index.html"):
        path = base / rel
        if await path.is_file():
            template = await path.read_text(encoding="utf-8")
            break
    if template is None:
        return _error("SPA v2 index.html not found", status=404)
    body = inject_app_name(template, app_name)
    return web.Response(text=body, content_type="text/html", charset="utf-8")


async def _handle_favicon(request: web.Request) -> web.FileResponse:
    favicon_path: str = request.app["favicon_path"]
    logger.debug(f"Serving favicon from {favicon_path!r}")
    return web.FileResponse(favicon_path)


async def _request_attention(request: web.Request) -> web.Response:
    """SPA pings this when a background chat turn finishes — flash tray/webview."""
    attention: AttentionHub = request.app["attention"]
    # schedule_notify is non-blocking; do not await tray pulse on the request path.
    attention.schedule_notify()
    return _json({"ok": True})


def _json(data: object, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
        status=status,
    )


def _error(message: str, status: int) -> web.Response:
    return _json({"error": message}, status=status)


async def create_app(
    aim: AIManager,
    sm: SessionManager,
    tm: TitleManager,
    favicon_path: str | None = None,
    app_name: str = DEFAULT_APP_NAME,
    attention: AttentionHub | None = None,
    feishu_ai_id: str = "",
    feishu_workspace_root: str = "",
    app_data_root: str = "",
    default_agent: str = "",
    default_workspace: str = "",
) -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app["aim"] = aim
    app["sm"] = sm
    app["tm"] = tm
    app["fm"] = FeishuManager(_sm=sm, _ai_id=feishu_ai_id, _workspace_root=feishu_workspace_root)
    app["wm"] = WorkspaceManager()
    app["cm"] = ChatManager()
    override = app_data_root.strip() or None
    from psi_agent._app_paths import history_dir as app_history_dir

    app["hm"] = HistoryManager(history_root=app_history_dir(override=override))
    app["todom"] = TodoManager(app_data_root=override)
    app["favicon_path"] = favicon_path
    app["app_name"] = app_name
    app["attention"] = attention if attention is not None else AttentionHub()
    app["app_data_root"] = override or ""
    app["default_agent"] = default_agent
    app["default_workspace"] = default_workspace

    spa_dist = anyio.Path(__file__).parent / "spa" / "dist"
    spa_v2_dist = anyio.Path(__file__).parent / "spa-v2" / "dist"
    app.router.add_get("/spa/index.html", _handle_spa_index)
    if await spa_dist.exists():
        app.router.add_static("/spa/", str(spa_dist), show_index=False)
    app.router.add_get("/spa", _handle_spa)
    app.router.add_get("/spa/", _handle_spa)

    app.router.add_get("/spa-v2/index.html", _handle_spa_v2_index)
    if await spa_v2_dist.exists():
        app.router.add_static("/spa-v2/", str(spa_v2_dist), show_index=False)
        logger.info(f"SPA v2 (default) enabled, serving {spa_v2_dist}")
        app.router.add_get("/", _handle_spa_v2)
        app.router.add_get("/spa-v2", _handle_spa_v2)
        app.router.add_get("/spa-v2/", _handle_spa_v2)
    else:
        app.router.add_get("/", _handle_spa)
    if favicon_path is not None:
        logger.info(f"Favicon enabled, serving {favicon_path!r} at /favicon.ico")
        app.router.add_get("/favicon.ico", _handle_favicon)
    app.router.add_get("/openapi.json", _handle_openapi)
    app.router.add_post("/ais", _create_ai)
    app.router.add_delete("/ais/{ai_id}", _delete_ai)
    app.router.add_get("/ais", _list_ais)
    app.router.add_post("/sessions", _create_session)
    app.router.add_delete("/sessions/{session_id}", _delete_session)
    app.router.add_get("/sessions", _list_sessions)
    app.router.add_get("/titles", _list_titles)
    app.router.add_post("/titles", _set_title)
    app.router.add_post("/titles/generate", _generate_title)
    app.router.add_post("/ui/attention", _request_attention)
    app.router.add_get("/workspace/cwd", _get_cwd)
    app.router.add_get("/defaults", _get_defaults)
    app.router.add_get("/workspace/places", _list_workspace_places)
    app.router.add_get("/workspace/browse", _browse_workspace)
    app.router.add_get("/workspace/file", _read_workspace_file)
    app.router.add_get("/sessions/{session_id}/history", _get_history)
    app.router.add_get("/sessions/{session_id}/todos", _get_todos)
    app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
    app.router.add_post("/feishu/route", _feishu_route)
    app.router.add_get("/feishu/routes", _list_feishu_routes)

    return app


async def _create_ai(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    try:
        body = await request.json()
        info = await aim.create(
            provider=body["provider"],
            model=body["model"],
            api_key=body["api_key"],
            base_url=body["base_url"],
            id=body.get("id", ""),
        )
        return _json(asdict(info), status=201)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error creating AI: {e!r}")
        return _error(str(e), status=500)


async def _delete_ai(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    ai_id = request.match_info["ai_id"]
    try:
        await aim.delete(ai_id)
        return _json({"id": ai_id, "status": "stopped"})
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error deleting AI {ai_id!r}: {e!r}")
        return _error(str(e), status=500)


async def _list_ais(request: web.Request) -> web.Response:
    aim: AIManager = request.app["aim"]
    return _json([asdict(i) for i in await aim.list_all()])


async def _create_session(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    try:
        body = await request.json()
        info = await sm.create(
            ai_id=body["ai_id"],
            id=body.get("id", ""),
            workspace=body.get("workspace", ""),
            agent=body.get("agent", ""),
        )
        return _json(asdict(info), status=201)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error creating session: {e!r}")
        return _error(str(e), status=500)


async def _delete_session(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    hm: HistoryManager = request.app["hm"]
    tm: TitleManager = request.app["tm"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
        await sm.delete(session_id)
        await hm.delete(workspace, session_id)
        from psi_agent._history_meta import remove_history_meta

        await remove_history_meta(session_id=session_id, app_data_override=request.app.get("app_data_root") or None)
        await tm.delete(session_id)
        return _json({"id": session_id, "status": "stopped"})
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error deleting session {session_id!r}: {e!r}")
        return _error(str(e), status=500)


async def _list_sessions(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    return _json([asdict(i) for i in await sm.list_all()])


async def _feishu_route(request: web.Request) -> web.Response:
    """按飞书 ``open_id`` 幂等地路由到其独立 Session, 首次见到时按需 spawn。

    body: ``{open_id, ai_id?, workspace?}`` → ``201 {open_id, session_id, channel_socket}``。
    channel 拿回 ``channel_socket`` 连接即得该用户隔离的会话。
    """
    fm: FeishuManager = request.app["fm"]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return _error("Request body must be a JSON object", status=400)
        socket, session_id = await fm.route(
            body["open_id"],
            ai_id=body.get("ai_id"),
            workspace=body.get("workspace"),
        )
        return _json({"open_id": body["open_id"], "session_id": session_id, "channel_socket": socket}, status=201)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error routing feishu open_id: {e!r}")
        return _error(str(e), status=500)


async def _list_feishu_routes(request: web.Request) -> web.Response:
    fm: FeishuManager = request.app["fm"]
    return _json([asdict(r) for r in fm.list_routes()])


async def _list_titles(request: web.Request) -> web.Response:
    tm: TitleManager = request.app["tm"]
    return _json(tm.get_all())


async def _set_title(request: web.Request) -> web.Response:
    tm: TitleManager = request.app["tm"]
    try:
        body = await request.json()
        sid = body["id"]
        await tm.set(sid, body["title"])
        return _json({"id": sid, "title": body["title"]})
    except (KeyError, TypeError) as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error setting title: {e!r}")
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
    logger.warning(f"Title generation returned no result for session {sid!r}")
    return _error("Failed to generate title", status=500)


async def _get_cwd(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app["wm"]
    return _json({"cwd": wm.get_cwd()})


async def _get_defaults(request: web.Request) -> web.Response:
    """Default agent / workspace / AppData roots for SPA and tooling."""
    from psi_agent._app_paths import (
        app_data_root,
        default_agent_path,
        default_workspace_path,
        history_dir,
        state_dir,
    )

    override = request.app.get("app_data_root") or None
    agent = request.app.get("default_agent") or str(default_agent_path())
    workspace = request.app.get("default_workspace") or str(default_workspace_path())
    return _json(
        {
            "agent": agent,
            "workspace": workspace,
            "app_data_root": str(app_data_root(override=override)),
            "history_dir": str(history_dir(override=override)),
            "state_dir": str(state_dir(override=override)),
        }
    )


async def _list_workspace_places(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app["wm"]
    return _json(await wm.list_places())


async def _browse_workspace(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app["wm"]
    path = request.query.get("path") or str(anyio.Path.cwd())
    kind = request.query.get("kind") or "directory"
    q = request.query.get("q") or ""
    try:
        return _json(await wm.browse(path, kind=kind, q=q))
    except (OSError, PermissionError, FileNotFoundError, NotADirectoryError) as e:
        return _error(str(e), status=400)


async def _read_workspace_file(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app["wm"]
    path = request.query.get("path") or ""
    root = request.query.get("root") or ""
    try:
        return _json(await wm.read_file(path, root=root))
    except ValueError as e:
        return _error(str(e), status=400)
    except FileNotFoundError as e:
        return _error(str(e), status=404)
    except PermissionError as e:
        return _error(str(e), status=403)
    except (OSError, IsADirectoryError) as e:
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


async def _get_todos(request: web.Request) -> web.Response:
    """Read AppData ``todos/{session_id}.json`` written by the ``todo`` tool (legacy workspace ``.psi/todos`` fallback)."""
    sm: SessionManager = request.app["sm"]
    todom: TodoManager = request.app["todom"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    return _json(await todom.get(workspace, session_id))


async def _handle_chat(request: web.Request) -> web.StreamResponse:
    sm: SessionManager = request.app["sm"]
    cm: ChatManager = request.app["cm"]
    session_id = request.match_info["session_id"]
    try:
        channel_socket = sm.get_socket(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)

    try:
        if request.content_type and "multipart" in request.content_type:
            data = await request.post()
            raw = data.get("chunks")
            raw_chunks = json.loads(str(raw)) if raw else []
            if not isinstance(raw_chunks, list):
                return _error("chunks must be a JSON array", status=400)
            body: dict[str, Any] = {"chunks": raw_chunks}
            for file_field in data.getall("file", []):
                fname = getattr(file_field, "filename", None)
                if fname:
                    content = await anyio.to_thread.run_sync(file_field.file.read)  # ty: ignore
                    data_b64 = b64encode(content).decode()
                    body["chunks"].append({"type": "blob", "name": fname, "data": data_b64})
        else:
            body = await request.json()
            if not isinstance(body, dict):
                return _error("Request body must be a JSON object", status=400)
    except (ValueError, TypeError) as e:
        return _error(f"Invalid request: {e}", status=400)

    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    try:
        await resp.prepare(request)
    except Exception:
        logger.warning(f"Failed to prepare SSE response for session {session_id!r}, client likely disconnected")
        return resp

    try:
        # Long tool / first-token waits yield nothing for minutes; keep the browser
        # fetch alive with SSE comments (ignored by readSSE) without cancelling
        # the upstream ChatManager generator — see `_write_chat_sse_with_keepalive`.
        await _write_chat_sse_with_keepalive(
            resp,
            cm.handle(channel_socket, body),
            session_id=session_id,
        )
    except Exception as e:
        logger.warning(f"Chat error for session {session_id!r}: {e!r}")
        with suppress(Exception):
            await resp.write(f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n".encode())
    finally:
        with suppress(Exception):
            await resp.write(b"data: [DONE]\n\n")
    return resp
