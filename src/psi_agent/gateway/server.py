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

from psi_agent.gateway._defaults import (
    resolve_appdata_root,
    resolve_default_agent,
    resolve_default_workspace,
)
from psi_agent.gateway._openapi import render_openapi
from psi_agent.i18n import DEFAULT_LANGUAGE, normalize_language
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._chat_manager import ChatManager
from psi_agent.runtime._history_manager import HistoryManager
from psi_agent.runtime._router_manager import RouterDependencyError, RouterManager, RouterUpstreamInfo
from psi_agent.runtime._scheduler_manager import SchedulerManager
from psi_agent.runtime._session_manager import SessionInfo, SessionManager
from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager
from psi_agent.runtime._todo_manager import TodoManager

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
                    # Keepalive must still detect a gone client. Swallowing every
                    # write failure here left ChatManager / Session running after
                    # SPA Stop, so the early-committed user message stayed in
                    # history and the next send saw both the aborted question and
                    # the edited one (模型把两段话一起想).
                    try:
                        await resp.write(b": keepalive\n\n")
                    except Exception as e:
                        logger.info(f"Chat SSE client gone during keepalive for session {session_id!r}: {e!r}")
                        raise
                    logger.debug(f"Chat SSE keepalive for session {session_id!r}")
                    continue
                except anyio.EndOfStream:
                    break
                data = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await resp.write(data.encode())
                logger.debug(f"Chat SSE chunk: {data[:1000]}")


async def _handle_openapi(request: web.Request) -> web.Response:
    """只报本进程真的注册了的那批 path —— 旗子由各 ``register_*_routes`` 立。"""
    body = render_openapi(
        desktop=bool(request.app.get("openapi_desktop")),
        feishu=bool(request.app.get("openapi_feishu")),
        oauth=bool(request.app.get("openapi_oauth")),
    )
    return web.Response(text=body, content_type="application/json")


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

    A7 后唯一调用方在 ``desktop/_routes.py`` (``/auth/*`` 随装配函数搬去了 ToC 包)。
    留在骨架是因为它只认识「HTTP 请求体该怎么解」, 不认识登录 —— 与 ``_json`` / ``_error``
    同类, 换第三条产品线来一样要用。
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


async def create_core_app(
    aim: AIManager,
    sm: SessionManager,
    tm: TitleManager,
    rm: RouterManager | None = None,
    *,
    default_agent: str = "",
    default_workspace: str = "",
    language: str = DEFAULT_LANGUAGE,
    appdata: str = "",
    scheduler_ai_id: str = "",
    schedm: SchedulerManager | None = None,
    sum_m: SummaryManager | None = None,
) -> web.Application:
    """两条产品线共同的骨架: 内核 manager + 两边都要的路由。

    这里**只认识** ``runtime`` 那批 manager: 没有飞书、没有托盘、没有 Windows 盘符、
    没有登录, 也没有 OAuth 中继 (它随 ``/oauth/*`` 一起归 ``feishu/``, 理由见
    ``feishu/_oauth_manager.py`` 模块头)。产品专属的东西由调用方往上贴 ——

    .. code-block:: python

        app = await create_core_app(aim, sm, tm, rm=rm)   # ToC
        await register_desktop_routes(app, favicon_path=..., authm=...)

        app = await create_core_app(aim, sm, tm, rm=rm)   # ToB
        register_feishu_routes(app, feishu_ai_id=..., feishu_workspace_root=...)

    A7: 两个 ``register_*`` 住在各自产品包里 —— ``desktop/_routes.py`` 与
    ``feishu/_routes.py``, 调用方从那里取 (唯一生产调用点是 ``gateway/__init__.py``)。
    它们原先也在本文件, 于是骨架为了给它们备料反向依赖了 7 个产品符号 —— 那让「骨架不
    认识产品线」只剩纪律、没有结构。现在方向是单向的: 产品包依赖骨架, 骨架不碰产品包。
    判据命令见 ``gateway/AGENTS.md``「依赖方向」; 本文件里连一行产品包的 import 都不该有,
    所以上面示例刻意只写调用、不写 import 语句 (写了就会被判据的 grep 抓成违例)。

    改成这样之前是一个函数收 17 个参数、内部判断, 结果桌面端容器里也建 ``FeishuManager``、
    飞书容器里也建 Windows 盘符枚举器 (原 ``create_app`` 的 ``app["fm"]`` / ``app["wm"]``
    两行都是无条件的)。
    """
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app["aim"] = aim
    app["rm"] = rm
    app["sm"] = sm
    app["tm"] = tm
    app["sum_m"] = sum_m if sum_m is not None else SummaryManager()
    # Owns the scheduler Sessions: one per workspace, created on demand, hidden
    # from SPA / state. Gateway.run passes its own instance (also needed by
    # startup restore); standalone tests may omit it.
    app["schedm"] = schedm or SchedulerManager(_sm=sm, _ai_id=scheduler_ai_id)
    app["cm"] = ChatManager()
    app["hm"] = HistoryManager()
    app["todom"] = TodoManager()
    app["default_agent"] = default_agent
    app["default_workspace"] = default_workspace
    app["language"] = normalize_language(language)
    app["appdata"] = appdata
    # ``GET /openapi.json`` 只报本进程真的注册了的那些 path —— 各 register_* 把自己
    # 那面旗子立起来 (见 ``_openapi.build_openapi_spec`` 的三个开关)。
    app["openapi_desktop"] = False
    app["openapi_feishu"] = False
    # ``/oauth/*`` 与产品线正交, 但仍由 ``register_oauth_routes`` 立旗 —— 骨架单独建的
    # app (测试、只用 REST 的调用方) 没贴过那两条路由, spec 里也不该报。
    app["openapi_oauth"] = False

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
    app.router.add_get("/defaults", _get_defaults)
    app.router.add_get("/sessions/{session_id}/history", _get_history)
    app.router.add_get("/sessions/{session_id}/todos", _get_todos)
    app.router.add_get("/sessions/{session_id}/todo-segments", _list_todo_segments)
    app.router.add_get("/sessions/{session_id}/todo-segments/{segment_id}", _get_todo_segment)
    app.router.add_post("/sessions/{session_id}/todo-segments/{segment_id}", _set_todo_segment_label)
    app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
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
            max_context_tokens=int(body.get("max_context_tokens", -1)),
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


async def _create_router(request: web.Request) -> web.Response:
    rm: RouterManager | None = request.app["rm"]
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
    rm: RouterManager | None = request.app["rm"]
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
    rm: RouterManager | None = request.app["rm"]
    return _json([] if rm is None else [asdict(info) for info in await rm.list_all()])


async def _create_session(request: web.Request) -> web.Response:
    """POST /sessions — Step 2 accepts optional ``agent`` (else Gateway default)."""
    sm: SessionManager = request.app["sm"]
    schedm: SchedulerManager = request.app["schedm"]
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
    sm: SessionManager = request.app["sm"]
    hm: HistoryManager = request.app["hm"]
    tm: TitleManager = request.app["tm"]
    sum_m: SummaryManager = request.app["sum_m"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
        await sm.delete(session_id)
        appdata = str(request.app.get("appdata") or "")
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
    sm: SessionManager = request.app["sm"]
    return _json([_session_data(info) for info in await sm.list_all()])


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


async def _session_ai_socket(request: web.Request, sid: str) -> str:
    """Resolve the AI socket used for title/summary generation for *sid*."""
    aim: AIManager = request.app["aim"]
    sm: SessionManager = request.app["sm"]
    sessions = await sm.list_all()
    sess = next((s for s in sessions if s.id == sid), None)
    if not sess:
        raise LookupError("Session not found")
    if sess.backend_type == "ai":
        return aim.get_socket(sess.backend_id)
    rm: RouterManager | None = request.app["rm"]
    if rm is None:
        raise LookupError("Router manager is not configured")
    info = rm.get(sess.backend_id)
    if info.mode == "fallback":
        return rm.get_socket(sess.backend_id)
    if info.router_ai_id is None:
        raise LookupError("Router control AI is not configured")
    return aim.get_socket(info.router_ai_id)


async def _generate_title(request: web.Request) -> web.Response:
    tm: TitleManager = request.app["tm"]
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
    sum_m: SummaryManager = request.app["sum_m"]
    return _json(sum_m.get_all())


async def _set_summary(request: web.Request) -> web.Response:
    sum_m: SummaryManager = request.app["sum_m"]
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
    sum_m: SummaryManager = request.app["sum_m"]
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


async def _get_defaults(request: web.Request) -> web.Response:
    """GET /defaults — shared path defaults for Session creators + AppData announce.

    Returns ``{agent, workspace, appdata}``. ``appdata`` is the memory-area root
    that later PRs will use for history / Gateway state / todos; this step does
    not relocate writers. Clients may omit ``agent`` on POST /sessions;
    SessionManager still applies the same default.
    """
    agent = request.app.get("default_agent") or await resolve_default_agent()
    workspace = request.app.get("default_workspace") or await resolve_default_workspace()
    appdata = request.app.get("appdata") or await resolve_appdata_root()
    prefs = request.app.get("uiprefs")
    saved = await prefs.language() if prefs is not None else ""
    language = normalize_language(saved or request.app.get("language") or DEFAULT_LANGUAGE)
    return _json({"agent": agent, "workspace": workspace, "appdata": appdata, "language": language})


async def _get_history(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    hm: HistoryManager = request.app["hm"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    messages = await hm.get(workspace, session_id, appdata=str(request.app.get("appdata") or ""))
    return _json(messages)


async def _get_todos(request: web.Request) -> web.Response:
    """Read session todos (AppData preferred; legacy workspace path dual-read)."""
    sm: SessionManager = request.app["sm"]
    todom: TodoManager = request.app["todom"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    appdata = str(request.app.get("appdata") or "")
    return _json(await todom.get(workspace, session_id, appdata=appdata))


async def _list_todo_segments(request: web.Request) -> web.Response:
    """List todo sub-task segments for a session (newest first)."""
    sm: SessionManager = request.app["sm"]
    todom: TodoManager = request.app["todom"]
    session_id = request.match_info["session_id"]
    if not sm.has(session_id):
        return _error(f"Session '{session_id}' not found", status=404)
    appdata = str(request.app.get("appdata") or "")
    return _json(await todom.list_segments(session_id, appdata=appdata))


async def _get_todo_segment(request: web.Request) -> web.Response:
    """Get one todo segment including todos[]."""
    sm: SessionManager = request.app["sm"]
    todom: TodoManager = request.app["todom"]
    session_id = request.match_info["session_id"]
    segment_id = request.match_info["segment_id"]
    if not sm.has(session_id):
        return _error(f"Session '{session_id}' not found", status=404)
    appdata = str(request.app.get("appdata") or "")
    seg = await todom.get_segment(session_id, segment_id, appdata=appdata)
    if seg is None:
        return _error(f"Todo segment '{segment_id}' not found", status=404)
    return _json(seg)


async def _set_todo_segment_label(request: web.Request) -> web.Response:
    """P1: patch segment label (e.g. from turn summary). Body: {label}."""
    sm: SessionManager = request.app["sm"]
    todom: TodoManager = request.app["todom"]
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
    appdata = str(request.app.get("appdata") or "")
    seg = await todom.set_segment_label(session_id, segment_id, label, appdata=appdata)
    if seg is None:
        return _error(f"Todo segment '{segment_id}' not found", status=404)
    return _json(seg)


async def _handle_chat(request: web.Request) -> web.StreamResponse:
    """裸 ``POST /sessions/{id}/chat`` —— **不做任何身份校验**, 见 ``_serve_chat_sse`` 模块内注释。"""
    return await _serve_chat_sse(request, request.match_info["session_id"])


async def _serve_chat_sse(request: web.Request, session_id: str) -> web.StreamResponse:
    """把一次聊天请求跑成 SSE 流。**鉴权由调用方负责, 本函数一行都不做。**

    抽出来是为了让带鉴权的那条 (``POST /feishu/sessions/{id}/chat``, 见
    ``feishu/_routes.py``) 与裸的这条共用同一份实现: 复制一份函数体的话, multipart 解析、
    keepalive、``[DONE]`` 收尾这些细节必然有一份先过时, 而过时的那份是**能驱动 agent 执行
    工具**的路径。

    ``session_id`` 由参数传入而非从 ``match_info`` 读: 两条路由的参数名恰好同为
    ``session_id``, 但让本函数去认某个路由的占位符名字, 等于把调用方的路由形状写进内核。

    骨架这条路由本身不鉴权是有意的 —— 它在容器内回环 8080 服务本机 (channel、工具链),
    公网暴露面由反代白名单决定, 不在这里加一层半真半假的判定。
    """
    sm: SessionManager = request.app["sm"]
    cm: ChatManager = request.app["cm"]
    try:
        channel_socket = sm.get_socket(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)

    try:
        if request.content_type and "multipart" in request.content_type:
            data = await request.post()
            raw = data.get("chunks")
            if isinstance(raw, str):
                raw_chunks = json.loads(raw)
            elif isinstance(raw, list):
                raw_chunks = raw
            else:
                raw_chunks = []
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
