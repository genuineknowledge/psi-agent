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

from psi_agent._logging import trace_context, trace_id_var
from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._attention import AttentionHub
from psi_agent.gateway._auth_manager import AuthManager
from psi_agent.gateway._chat_manager import ChatManager
from psi_agent.gateway._defaults import (
    resolve_appdata_root,
    resolve_default_agent,
    resolve_default_workspace,
)
from psi_agent.gateway._feishu_manager import FeishuManager
from psi_agent.gateway._history_manager import HistoryManager
from psi_agent.gateway._keys import (
    AIM_KEY,
    APP_NAME_KEY,
    APPDATA_KEY,
    ATTENTION_KEY,
    AUTHM_KEY,
    CM_KEY,
    DEFAULT_AGENT_KEY,
    DEFAULT_WORKSPACE_KEY,
    FAVICON_PATH_KEY,
    FM_KEY,
    HM_KEY,
    OAUTH_KEY,
    RM_KEY,
    SCHEDM_KEY,
    SM_KEY,
    SUM_M_KEY,
    TM_KEY,
    TODOM_KEY,
    WM_KEY,
)
from psi_agent.gateway._oauth_manager import OAuthRelay
from psi_agent.gateway._openapi import render_openapi
from psi_agent.gateway._router_manager import RouterDependencyError, RouterManager, RouterUpstreamInfo
from psi_agent.gateway._scheduler_manager import SchedulerManager
from psi_agent.gateway._session_manager import SessionInfo, SessionManager
from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME, inject_app_name, read_spa_index_template
from psi_agent.gateway._summary_manager import SummaryManager
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
    app_name: str = request.app[APP_NAME_KEY]
    template = await read_spa_index_template()
    if template is None:
        return _error("SPA index.html not found", status=404)
    body = inject_app_name(template, app_name)
    return web.Response(text=body, content_type="text/html", charset="utf-8")


def _gateway_spa_root() -> anyio.Path:
    """Package dir that owns ``spa/`` and ``spa-v2/`` (tests may monkeypatch)."""
    return anyio.Path(__file__).parent


async def _handle_spa_v2_index(request: web.Request) -> web.Response:
    app_name: str = request.app[APP_NAME_KEY]
    base = _gateway_spa_root() / "spa-v2"
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
    favicon_path: str = request.app[FAVICON_PATH_KEY]
    logger.debug(f"Serving favicon from {favicon_path!r}")
    return web.FileResponse(favicon_path)


async def _request_attention(request: web.Request) -> web.Response:
    """SPA pings this when a background chat turn finishes — flash tray/webview."""
    attention: AttentionHub = request.app[ATTENTION_KEY]
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


async def _read_json(request: web.Request) -> dict[str, Any] | None:
    """读 JSON 请求体; 非法或非对象返回 ``None`` 让调用方回 400。

    ``/auth/*`` 用它而非直接 ``await request.json()``: 认证接口面向 SPA 表单,
    非法 JSON 应当是清晰的 400, 而不是被 except 兜成 500。
    """
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _session_data(info: SessionInfo) -> dict[str, Any]:
    data = asdict(info)
    if data.get("backend_type") == "ai":
        data["ai_id"] = data["backend_id"]
    # ``scheduler`` is a property derived from active_schedules, so asdict omits
    # it — add it back explicitly; the REST / SPA contract is unchanged.
    data["active_schedules"] = list(info.active_schedules)
    data["deactive_schedules"] = list(info.deactive_schedules)
    data["scheduler"] = info.scheduler
    return data


@web.middleware
async def trace_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    async with trace_context(request):
        trace_id = trace_id_var.get()
        try:
            response = await handler(request)
            response.headers["X-Trace-ID"] = trace_id
            return response
        except web.HTTPException as e:
            e.headers["X-Trace-ID"] = trace_id
            raise


async def create_app(
    aim: AIManager,
    sm: SessionManager,
    tm: TitleManager,
    rm: RouterManager | None = None,
    favicon_path: str | None = None,
    app_name: str = DEFAULT_APP_NAME,
    attention: AttentionHub | None = None,
    feishu_ai_id: str = "",
    feishu_workspace_root: str = "",
    default_agent: str = "",
    default_workspace: str = "",
    appdata: str = "",
    scheduler_ai_id: str = "",
    schedm: SchedulerManager | None = None,
    sum_m: SummaryManager | None = None,
    authm: AuthManager | None = None,
) -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024, middlewares=[trace_middleware])
    app[AIM_KEY] = aim
    app[RM_KEY] = rm
    app[SM_KEY] = sm
    app[TM_KEY] = tm
    app[SUM_M_KEY] = sum_m if sum_m is not None else SummaryManager()
    # Owns the scheduler Sessions: one per workspace, created on demand, hidden
    # from SPA / state. Gateway.run passes its own instance (also needed by
    # startup restore); standalone tests may omit it.
    app[SCHEDM_KEY] = schedm or SchedulerManager(_sm=sm, _ai_id=scheduler_ai_id or feishu_ai_id)
    app[FM_KEY] = FeishuManager(_sm=sm, _ai_id=feishu_ai_id, _workspace_root=feishu_workspace_root)
    app[OAUTH_KEY] = OAuthRelay()
    app[WM_KEY] = WorkspaceManager()
    app[CM_KEY] = ChatManager()
    app[HM_KEY] = HistoryManager()
    app[TODOM_KEY] = TodoManager()
    app[FAVICON_PATH_KEY] = favicon_path
    app[APP_NAME_KEY] = app_name
    app[ATTENTION_KEY] = attention if attention is not None else AttentionHub()
    app[DEFAULT_AGENT_KEY] = default_agent
    app[DEFAULT_WORKSPACE_KEY] = default_workspace
    app[APPDATA_KEY] = appdata

    spa_root = _gateway_spa_root()
    spa_dist = spa_root / "spa" / "dist"
    spa_v2_dist = spa_root / "spa-v2" / "dist"
    # Register directory redirects before add_static: aiohttp matches static
    # ``/spa-v2/`` first when registered earlier, and show_index=False → 403.
    app.router.add_get("/spa/index.html", _handle_spa_index)
    app.router.add_get("/spa", _handle_spa)
    app.router.add_get("/spa/", _handle_spa)
    if await spa_dist.exists():
        app.router.add_static("/spa/", str(spa_dist), show_index=False)

    app.router.add_get("/spa-v2/index.html", _handle_spa_v2_index)
    if await spa_v2_dist.exists():
        logger.info(f"SPA v2 (default) enabled, serving {spa_v2_dist}")
        app.router.add_get("/", _handle_spa_v2)
        app.router.add_get("/spa-v2", _handle_spa_v2)
        app.router.add_get("/spa-v2/", _handle_spa_v2)
        app.router.add_static("/spa-v2/", str(spa_v2_dist), show_index=False)
    else:
        app.router.add_get("/", _handle_spa)
    if favicon_path is not None:
        logger.info(f"Favicon enabled, serving {favicon_path!r} at /favicon.ico")
        app.router.add_get("/favicon.ico", _handle_favicon)
    app.router.add_get("/openapi.json", _handle_openapi)
    app.router.add_post("/ais", _create_ai)
    app.router.add_delete("/ais/{ai_id}", _delete_ai)
    app.router.add_get("/ais", _list_ais)
    app.router.add_post("/routers", _create_router)
    app.router.add_delete("/routers/{router_id}", _delete_router)
    app.router.add_get("/routers", _list_routers)
    app.router.add_post("/sessions", _create_session)
    app.router.add_delete("/sessions/{session_id}", _delete_session)
    app.router.add_get("/sessions", _list_sessions)
    app.router.add_get("/titles", _list_titles)
    app.router.add_post("/titles", _set_title)
    app.router.add_post("/titles/generate", _generate_title)
    app.router.add_get("/summaries", _list_summaries)
    app.router.add_post("/summaries", _set_summary)
    app.router.add_post("/summaries/generate", _generate_summary)
    app.router.add_post("/ui/attention", _request_attention)
    app.router.add_get("/workspace/cwd", _get_cwd)
    app.router.add_get("/defaults", _get_defaults)
    app.router.add_get("/workspace/places", _list_workspace_places)
    app.router.add_get("/workspace/browse", _browse_workspace)
    app.router.add_get("/workspace/file", _read_workspace_file)
    app.router.add_post("/workspace/reveal", _reveal_workspace_path)
    app.router.add_get("/sessions/{session_id}/history", _get_history)
    app.router.add_get("/sessions/{session_id}/todos", _get_todos)
    app.router.add_get("/sessions/{session_id}/todo-segments", _list_todo_segments)
    app.router.add_get("/sessions/{session_id}/todo-segments/{segment_id}", _get_todo_segment)
    app.router.add_post("/sessions/{session_id}/todo-segments/{segment_id}", _set_todo_segment_label)
    app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
    app.router.add_post("/feishu/route", _feishu_route)
    app.router.add_get("/feishu/routes", _list_feishu_routes)
    app.router.add_get("/oauth/callback", _oauth_callback)
    app.router.add_get("/oauth/code", _oauth_take_code)

    # 认证路由: 只在配了云端地址时才注册。authm 为 None 时**一条都不注册**,
    # 现有本地单用户流程零回归。
    if authm is not None:
        app[AUTHM_KEY] = authm
        app.router.add_get("/auth/status", _auth_status)
        app.router.add_post("/auth/send-code", _auth_send_code)
        app.router.add_post("/auth/verify", _auth_verify)
        app.router.add_post("/auth/complete", _auth_complete)
        app.router.add_post("/auth/bind", _auth_bind)
        app.router.add_delete("/auth/identities/{provider}", _auth_unbind)
        app.router.add_get("/auth/me", _auth_me)
        app.router.add_post("/auth/logout", _auth_logout)
        app.router.add_get("/auth/devices", _auth_devices)
        app.router.add_delete("/auth/devices/{device_id}", _auth_revoke_device)
        logger.info(f"Auth enabled, proxying to {authm.endpoint}{authm.prefix}")

    return app


async def _create_ai(request: web.Request) -> web.Response:
    aim: AIManager = request.app[AIM_KEY]
    try:
        body = await request.json()
        info = await aim.create(
            provider=body["provider"],
            model=body["model"],
            api_key=body["api_key"],
            base_url=body["base_url"],
            id=body.get("id", ""),
            max_context_tokens=int(body.get("max_context_tokens", -1)),
        )
        return _json(asdict(info), status=201)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error creating AI: {e!r}")
        return _error(str(e), status=500)


async def _delete_ai(request: web.Request) -> web.Response:
    aim: AIManager = request.app[AIM_KEY]
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
    aim: AIManager = request.app[AIM_KEY]
    return _json([asdict(i) for i in await aim.list_all()])


async def _create_router(request: web.Request) -> web.Response:
    rm: RouterManager | None = request.app[RM_KEY]
    if rm is None:
        return _error("Router manager is not configured", status=503)
    try:
        body = await request.json()
        info = await rm.create(
            name=body["name"],
            mode=body["mode"],
            router_ai_id=body["router_ai_id"],
            upstreams=[
                RouterUpstreamInfo(
                    backend_type=item["backend_type"],
                    backend_id=item["backend_id"],
                    description=item["description"],
                )
                for item in body["upstreams"]
            ],
            router_timeout=body.get("router_timeout"),
            target_timeout=body.get("target_timeout"),
            max_context_chars=body.get("max_context_chars", 12_000),
            id=body.get("id", ""),
        )
        return _json(asdict(info), status=201)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error creating Router: {e!r}")
        return _error(str(e), status=500)


async def _delete_router(request: web.Request) -> web.Response:
    rm: RouterManager | None = request.app[RM_KEY]
    if rm is None:
        return _error("Router manager is not configured", status=503)
    router_id = request.match_info["router_id"]
    try:
        await rm.delete(router_id)
        return _json({"id": router_id, "status": "stopped"})
    except RouterDependencyError as e:
        return _error(str(e), status=409)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error deleting Router {router_id!r}: {e!r}")
        return _error(str(e), status=500)


async def _list_routers(request: web.Request) -> web.Response:
    rm: RouterManager | None = request.app[RM_KEY]
    return _json([] if rm is None else [asdict(info) for info in await rm.list_all()])


async def _create_session(request: web.Request) -> web.Response:
    """POST /sessions — Step 2 accepts optional ``agent`` (else Gateway default)."""
    sm: SessionManager = request.app[SM_KEY]
    schedm: SchedulerManager = request.app[SCHEDM_KEY]
    try:
        body = await request.json()
        backend_type = body.get("backend_type", "ai")
        backend_id = body.get("backend_id", body.get("ai_id", ""))
        info = await sm.create(
            backend_type=backend_type,
            backend_id=backend_id,
            id=body.get("id", ""),
            workspace=body.get("workspace", ""),
            agent=body.get("agent", ""),
        )
        # This workspace's schedules are owned by its dedicated scheduler
        # Session, not fired by this session.
        await schedm.ensure(info.workspace, ai_id=info.backend_id, agent=info.agent)
        return _json(_session_data(info), status=201)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error creating session: {e!r}")
        return _error(str(e), status=500)


async def _delete_session(request: web.Request) -> web.Response:
    sm: SessionManager = request.app[SM_KEY]
    hm: HistoryManager = request.app[HM_KEY]
    tm: TitleManager = request.app[TM_KEY]
    sum_m: SummaryManager = request.app[SUM_M_KEY]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
        await sm.delete(session_id)
        appdata = str(request.app.get(APPDATA_KEY) or "")
        await hm.delete(workspace, session_id, appdata=appdata)
        await tm.delete(session_id)
        await sum_m.delete(session_id)
        return _json({"id": session_id, "status": "stopped"})
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error deleting session {session_id!r}: {e!r}")
        return _error(str(e), status=500)


async def _list_sessions(request: web.Request) -> web.Response:
    sm: SessionManager = request.app[SM_KEY]
    return _json([_session_data(info) for info in await sm.list_all()])


async def _feishu_route(request: web.Request) -> web.Response:
    """幂等地把一次飞书会话路由到其 Session, 首次见到时按需 spawn。

    body: ``{open_id, chat_id?, chat_type?, ai_id?, workspace?}`` →
    ``201 {open_id, chat_id, session_id, channel_socket}``。群聊 (``chat_type`` 为 group/topic
    且 ``chat_id`` 非空) 整群共用一个 Session, 其余按 ``open_id`` 一人一个。channel 拿回
    ``channel_socket`` 连接即得对应会话。
    """
    fm: FeishuManager = request.app[FM_KEY]
    schedm: SchedulerManager = request.app[SCHEDM_KEY]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return _error("Request body must be a JSON object", status=400)
        open_id = body.get("open_id") or ""
        chat_id = body.get("chat_id") or ""
        chat_type = body.get("chat_type") or ""
        socket, session_id = await fm.route(
            open_id,
            chat_id=chat_id,
            chat_type=chat_type,
            ai_id=body.get("ai_id"),
            workspace=body.get("workspace"),
        )
        # Schedules under this session's workspace belong to its dedicated scheduler
        # Session, not to the user/group session.
        sm: SessionManager = request.app[SM_KEY]
        await schedm.ensure(
            sm.get_workspace(session_id),
            ai_id=sm.get_backend_id(session_id),
            agent=sm.get_agent(session_id),
        )
        return _json(
            {
                "open_id": open_id,
                "chat_id": chat_id,
                "session_id": session_id,
                "channel_socket": socket,
            },
            status=201,
        )
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    except Exception as e:
        logger.error(f"Unexpected error routing feishu open_id: {e!r}")
        return _error(str(e), status=500)


async def _list_feishu_routes(request: web.Request) -> web.Response:
    fm: FeishuManager = request.app[FM_KEY]
    return _json([asdict(r) for r in fm.list_routes()])


_OAUTH_DONE_HTML = (
    "<!doctype html><meta charset=utf-8><title>授权完成</title>"
    "<body style='font:16px/1.7 system-ui;padding:3rem;text-align:center'>"
    "<h2>{title}</h2><p style='color:#666'>{note}</p></body>"
)


def _oauth_html(title: str, note: str, status: int = 200) -> web.Response:
    return web.Response(
        text=_OAUTH_DONE_HTML.format(title=title, note=note),
        content_type="text/html",
        charset="utf-8",
        status=status,
    )


async def _oauth_callback(request: web.Request) -> web.Response:
    """OAuth 重定向落地点: 收下 ``?code=&state=`` 交给中继, 给用户一个成功页。

    发起方(workspace 工具)随后用同一个 ``state`` 去 ``/oauth/code`` 取回 —— 用户
    因此**不需要**再从地址栏手工复制 code。
    """
    relay: OAuthRelay = request.app[OAUTH_KEY]
    state = request.query.get("state", "")
    code = request.query.get("code", "")
    error = request.query.get("error", "") or request.query.get("error_description", "")
    if not state:
        return _oauth_html("授权链接不完整", "回调缺少 state 参数, 请回到对话里重新发起授权。", status=400)
    if not code and not error:
        error = "callback carried neither code nor error"
    await relay.deliver(state, code=code, error=error)
    if error:
        return _oauth_html("授权未完成", "可以回到对话里重新发起授权。", status=400)
    return _oauth_html("授权成功 ✅", "可以关掉这个页面, 回到对话继续 —— 不用复制任何东西。")


async def _oauth_take_code(request: web.Request) -> web.Response:
    """发起方取件: ``?state=`` 命中则返回 ``{code}`` 并作废, 未到达返回 404。"""
    relay: OAuthRelay = request.app[OAUTH_KEY]
    state = request.query.get("state", "")
    if not state:
        return _error("state query parameter is required", status=400)
    pending = await relay.take(state)
    if pending is None:
        return _error("no callback received for this state yet", status=404)
    if pending.error:
        return _json({"state": state, "error": pending.error}, status=200)
    return _json({"state": state, "code": pending.code}, status=200)


async def _list_titles(request: web.Request) -> web.Response:
    tm: TitleManager = request.app[TM_KEY]
    return _json(tm.get_all())


async def _set_title(request: web.Request) -> web.Response:
    tm: TitleManager = request.app[TM_KEY]
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


async def _session_ai_socket(request: web.Request, sid: str) -> str:
    """Resolve the AI socket used for title/summary generation for *sid*."""
    aim: AIManager = request.app[AIM_KEY]
    sm: SessionManager = request.app[SM_KEY]
    sessions = await sm.list_all()
    sess = next((s for s in sessions if s.id == sid), None)
    if not sess:
        raise LookupError("Session not found")
    if sess.backend_type == "ai":
        return aim.get_socket(sess.backend_id)
    rm: RouterManager | None = request.app[RM_KEY]
    if rm is None:
        raise LookupError("Router manager is not configured")
    info = rm.get(sess.backend_id)
    if info.mode == "fallback":
        return rm.get_socket(sess.backend_id)
    if info.router_ai_id is None:
        raise LookupError("Router control AI is not configured")
    return aim.get_socket(info.router_ai_id)


async def _generate_title(request: web.Request) -> web.Response:
    tm: TitleManager = request.app[TM_KEY]
    try:
        body = await request.json()
        sid = body["id"]
        user_text = body.get("user_text", "")
        assistant_text = body.get("assistant_text", "")
    except (KeyError, TypeError) as e:
        return _error(str(e), status=400)

    try:
        ai_socket = await _session_ai_socket(request, sid)
    except LookupError as e:
        return _error(str(e), status=404)

    title = await tm.generate(sid, ai_socket, user_text, assistant_text)
    if title:
        return _json({"id": sid, "title": title})
    logger.warning(f"Title generation returned no result for session {sid!r}")
    return _error("Failed to generate title", status=500)


async def _list_summaries(request: web.Request) -> web.Response:
    sum_m: SummaryManager = request.app[SUM_M_KEY]
    return _json(sum_m.get_all())


async def _set_summary(request: web.Request) -> web.Response:
    sum_m: SummaryManager = request.app[SUM_M_KEY]
    try:
        body = await request.json()
        sid = body["id"]
        await sum_m.set(sid, body["summary"])
        return _json({"id": sid, "summary": body["summary"]})
    except (KeyError, TypeError) as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error setting summary: {e!r}")
        return _error(str(e), status=500)


async def _generate_summary(request: web.Request) -> web.Response:
    sum_m: SummaryManager = request.app[SUM_M_KEY]
    try:
        body = await request.json()
        sid = body["id"]
        user_text = body.get("user_text", "")
        assistant_text = body.get("assistant_text", "")
    except (KeyError, TypeError) as e:
        return _error(str(e), status=400)

    try:
        ai_socket = await _session_ai_socket(request, sid)
    except LookupError as e:
        return _error(str(e), status=404)

    summary = await sum_m.generate(sid, ai_socket, user_text, assistant_text)
    if summary:
        return _json({"id": sid, "summary": summary})
    logger.warning(f"Summary generation returned no result for session {sid!r}")
    return _error("Failed to generate summary", status=500)


async def _get_cwd(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app[WM_KEY]
    return _json({"cwd": wm.get_cwd()})


async def _get_defaults(request: web.Request) -> web.Response:
    """GET /defaults — shared path defaults for Session creators + AppData announce.

    Returns ``{agent, workspace, appdata}``. ``appdata`` is the memory-area root
    that later PRs will use for history / Gateway state / todos; this step does
    not relocate writers. Clients may omit ``agent`` on POST /sessions;
    SessionManager still applies the same default.
    """
    agent = request.app.get(DEFAULT_AGENT_KEY) or await resolve_default_agent()
    workspace = request.app.get(DEFAULT_WORKSPACE_KEY) or await resolve_default_workspace()
    appdata = request.app.get(APPDATA_KEY) or await resolve_appdata_root()
    return _json({"agent": agent, "workspace": workspace, "appdata": appdata})


async def _list_workspace_places(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app[WM_KEY]
    return _json(await wm.list_places())


async def _browse_workspace(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app[WM_KEY]
    path = request.query.get("path") or str(anyio.Path.cwd())
    kind = request.query.get("kind") or "directory"
    q = request.query.get("q") or ""
    try:
        return _json(await wm.browse(path, kind=kind, q=q))
    except (OSError, PermissionError, FileNotFoundError, NotADirectoryError) as e:
        return _error(str(e), status=400)


async def _read_workspace_file(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app[WM_KEY]
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


async def _reveal_workspace_path(request: web.Request) -> web.Response:
    """POST /workspace/reveal — open OS file manager at path (select file if possible)."""
    wm: WorkspaceManager = request.app[WM_KEY]
    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body", status=400)
    if not isinstance(body, dict):
        return _error("Body must be a JSON object", status=400)
    path = body.get("path")
    if not isinstance(path, str):
        return _error("path is required", status=400)
    try:
        return _json(await wm.reveal(path))
    except ValueError as e:
        return _error(str(e), status=400)
    except FileNotFoundError as e:
        return _error(str(e), status=404)
    except OSError as e:
        return _error(str(e), status=400)


async def _get_history(request: web.Request) -> web.Response:
    sm: SessionManager = request.app[SM_KEY]
    hm: HistoryManager = request.app[HM_KEY]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    messages = await hm.get(workspace, session_id, appdata=str(request.app.get(APPDATA_KEY) or ""))
    return _json(messages)


async def _get_todos(request: web.Request) -> web.Response:
    """Read session todos (AppData preferred; legacy workspace path dual-read)."""
    sm: SessionManager = request.app[SM_KEY]
    todom: TodoManager = request.app[TODOM_KEY]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    appdata = str(request.app.get(APPDATA_KEY) or "")
    return _json(await todom.get(workspace, session_id, appdata=appdata))


async def _list_todo_segments(request: web.Request) -> web.Response:
    """List todo sub-task segments for a session (newest first)."""
    sm: SessionManager = request.app[SM_KEY]
    todom: TodoManager = request.app[TODOM_KEY]
    session_id = request.match_info["session_id"]
    if not sm.has(session_id):
        return _error(f"Session '{session_id}' not found", status=404)
    appdata = str(request.app.get(APPDATA_KEY) or "")
    return _json(await todom.list_segments(session_id, appdata=appdata))


async def _get_todo_segment(request: web.Request) -> web.Response:
    """Get one todo segment including todos[]."""
    sm: SessionManager = request.app[SM_KEY]
    todom: TodoManager = request.app[TODOM_KEY]
    session_id = request.match_info["session_id"]
    segment_id = request.match_info["segment_id"]
    if not sm.has(session_id):
        return _error(f"Session '{session_id}' not found", status=404)
    appdata = str(request.app.get(APPDATA_KEY) or "")
    seg = await todom.get_segment(session_id, segment_id, appdata=appdata)
    if seg is None:
        return _error(f"Todo segment '{segment_id}' not found", status=404)
    return _json(seg)


async def _set_todo_segment_label(request: web.Request) -> web.Response:
    """P1: patch segment label (e.g. from turn summary). Body: {label}."""
    sm: SessionManager = request.app[SM_KEY]
    todom: TodoManager = request.app[TODOM_KEY]
    session_id = request.match_info["session_id"]
    segment_id = request.match_info["segment_id"]
    if not sm.has(session_id):
        return _error(f"Session '{session_id}' not found", status=404)
    try:
        body = await request.json()
    except (ValueError, TypeError) as e:
        return _error(f"Invalid request: {e}", status=400)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object", status=400)
    label = body.get("label")
    if not isinstance(label, str) or not label.strip():
        return _error("label is required", status=400)
    appdata = str(request.app.get(APPDATA_KEY) or "")
    seg = await todom.set_segment_label(session_id, segment_id, label, appdata=appdata)
    if seg is None:
        return _error(f"Todo segment '{segment_id}' not found", status=404)
    return _json(seg)


async def _handle_chat(request: web.Request) -> web.StreamResponse:
    sm: SessionManager = request.app[SM_KEY]
    cm: ChatManager = request.app[CM_KEY]
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


# ---------------------------------------------------------------- 认证 (/auth/*)
#
# 这些路由只在云端地址非空时注册 (见 create_app)。为空时整套认证不加载, 现有本地
# 单用户流程零回归 —— 这是本期改动能安全落地的前提。
#
# Gateway 侧刻意只做**转发 + 本机凭证管理**: 不持任何供应商密钥 (安装包里放阿里云
# AK/SK 或 Resend key 等于公开发布), 不做授权判定 (用户本人即机器管理员, 客户端侧
# 校验可被绕过)。发码与鉴权都在云端。


def _auth(request: web.Request) -> AuthManager:
    return request.app[AUTHM_KEY]


def _auth_reply(status: int, body: dict[str, Any]) -> web.Response:
    """把云端响应原样转给 SPA。

    ``status == 0`` 表示云端不可达 —— 转成 502, 而不是把 0 当 HTTP 状态码
    (aiohttp 会抛), 也不掩饰成 200。
    """
    if status == 0:
        return _json(body, status=502)
    return _json(body, status=status)


async def _auth_status(request: web.Request) -> web.Response:
    """当前登录态。SPA 据此决定显示登录引导还是身份信息; 不含 token。"""
    return _json(_auth(request).status())


async def _auth_send_code(request: web.Request) -> web.Response:
    """请云端发验证码 (手机号或邮箱)。"""
    body = await _read_json(request)
    if body is None:
        return _error("invalid_request", status=400)
    status, data = await _auth(request).send_code(
        phone=str(body.get("phone", "")),
        email=str(body.get("email", "")),
    )
    return _auth_reply(status, data)


async def _auth_verify(request: web.Request) -> web.Response:
    """校验验证码。老用户直接登录; 新用户的 tempToken 由 manager 扣住不下发。"""
    body = await _read_json(request)
    if body is None:
        return _error("invalid_request", status=400)
    status, data = await _auth(request).verify(
        code=str(body.get("code", "")),
        phone=str(body.get("phone", "")),
        email=str(body.get("email", "")),
    )
    return _auth_reply(status, data)


async def _auth_bind(request: web.Request) -> web.Response:
    """已登录态下把手机号/邮箱绑定到当前账号。复用发码, 校验走云端 /identities/*。"""
    body = await _read_json(request)
    if body is None:
        return _error("invalid_request", status=400)
    status, data = await _auth(request).bind(
        code=str(body.get("code", "")),
        phone=str(body.get("phone", "")),
        email=str(body.get("email", "")),
    )
    return _auth_reply(status, data)


async def _auth_unbind(request: web.Request) -> web.Response:
    """解绑一种登录方式。云端拦截「解绑最后一个身份」并回 last_identity。"""
    status, data = await _auth(request).unbind(request.match_info.get("provider", ""))
    return _auth_reply(status, data)


async def _auth_complete(request: web.Request) -> web.Response:
    """两段式注册的第二段: 建号并换正式 token。tempToken 取自进程内暂存。"""
    body = await _read_json(request)
    if body is None:
        return _error("invalid_request", status=400)
    status, data = await _auth(request).complete(display_name=str(body.get("displayName", "")))
    return _auth_reply(status, data)


async def _auth_me(request: web.Request) -> web.Response:
    status, data = await _auth(request).me()
    return _auth_reply(status, data)


async def _auth_logout(request: web.Request) -> web.Response:
    status, data = await _auth(request).logout()
    return _auth_reply(status, data)


async def _auth_devices(request: web.Request) -> web.Response:
    """列出已登录设备。"""
    status, data = await _auth(request).list_devices()
    return _auth_reply(status, data)


async def _auth_revoke_device(request: web.Request) -> web.Response:
    """踢掉某台设备, 该设备下次请求即 401。"""
    status, data = await _auth(request).revoke_device(request.match_info.get("device_id", ""))
    return _auth_reply(status, data)
