"""ToC 路由装配 —— ``register_desktop_routes()`` 与它专属的那批 handler。

A7: 这个函数原先住在 ``gateway/server.py``, 于是骨架层为了给它备料, 反向 import 了 6 个
本包符号 (`AttentionHub` / `AuthManager` / `is_cloud_free_model` / `inject_app_name` /
`UIPrefs` / `WorkspaceManager`) 外加飞书的 `FeishuManager`。A5 要解决的正是「没有任何结构性
手段阻止下一次有人从骨架 import 产品模块」, 但装配函数留在骨架里, 骨架就**必须**认识两条
产品线 —— 缺口不在纪律上, 在文件归属上。搬进产品包后依赖方向单一: 产品包 → 骨架, 骨架
对本包一无所知 (判据见 ``gateway/AGENTS.md``「依赖方向」)。

``_json`` / ``_error`` 仍从骨架 import: 它们被核心 handler 用了一百多次, 是骨架自己的东西,
本层只是复用 —— 方向是产品 → 骨架, 正是允许的那一向。
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.gateway.desktop._attention import AttentionHub
from psi_agent.gateway.desktop._auth_manager import AuthManager
from psi_agent.gateway.desktop._free_model import is_cloud_free_model
from psi_agent.gateway.desktop._spa_shell import DEFAULT_APP_NAME, inject_app_name, read_spa_index_template
from psi_agent.gateway.desktop._ui_prefs import UIPrefs
from psi_agent.gateway.desktop._workspace_manager import WorkspaceManager
from psi_agent.gateway.server import _error, _json, _read_json
from psi_agent.runtime._ai_manager import AIManager


async def _handle_spa(request: web.Request) -> web.HTTPFound:
    raise web.HTTPFound("/spa/index.html")


async def _handle_spa_v2(request: web.Request) -> web.HTTPFound:
    raise web.HTTPFound("/spa-v2/index.html")


async def _handle_spa_index(request: web.Request) -> web.Response:
    app_name: str = request.app["app_name"]
    template = await read_spa_index_template()
    if template is None:
        return _error("SPA index.html not found", status=404)
    body = inject_app_name(template, app_name)
    return web.Response(text=body, content_type="text/html", charset="utf-8")


def _gateway_spa_root() -> anyio.Path:
    """Package dir that owns ``spa/`` and ``spa-v2/`` (tests may monkeypatch).

    A5: 两棵树随 ToC 其余部分搬进 ``gateway/desktop/``。A7 把装配函数也搬了进来, 所以
    这里直接从**本模块**的 ``__file__`` 推 —— 不再需要 import ``desktop`` 包对象绕一圈
    (那一圈原是因为调用方住在骨架里)。拼错则 SPA 静态资源静默 404: ``await
    spa_dist.exists()`` 为假时连 static 都不注册, 不报错。
    """
    return anyio.Path(__file__).parent


async def _handle_spa_v2_index(request: web.Request) -> web.Response:
    app_name: str = request.app["app_name"]
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
    favicon_path: str = request.app["favicon_path"]
    logger.debug(f"Serving favicon from {favicon_path!r}")
    return web.FileResponse(favicon_path)


async def _request_attention(request: web.Request) -> web.Response:
    """SPA pings this when a background chat turn finishes — flash tray/webview."""
    attention: AttentionHub = request.app["attention"]
    # schedule_notify is non-blocking; do not await tray pulse on the request path.
    attention.schedule_notify()
    return _json({"ok": True})


async def _get_survey_pref(request: web.Request) -> web.Response:
    """GET /ui/prefs/survey — has the user already dismissed the survey popup?

    Server-side because the SPA origin's port changes every startup (random port),
    which silently voids any ``localStorage`` flag. See ``_ui_prefs``.
    """
    prefs: UIPrefs = request.app["uiprefs"]
    return _json({"done": await prefs.survey_done()})


async def _set_survey_pref(request: web.Request) -> web.Response:
    """POST /ui/prefs/survey — record that the survey popup was dismissed.

    Body ``{"done": bool}``; missing/non-bool ``done`` is treated as ``true``
    since the only caller is the dismiss action.
    """
    prefs: UIPrefs = request.app["uiprefs"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    done = body.get("done") if isinstance(body, dict) else None
    await prefs.set_survey_done(done if isinstance(done, bool) else True)
    return _json({"done": await prefs.survey_done()})


async def _get_cwd(request: web.Request) -> web.Response:
    wm: WorkspaceManager = request.app["wm"]
    return _json({"cwd": wm.get_cwd()})


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


async def _reveal_workspace_path(request: web.Request) -> web.Response:
    """POST /workspace/reveal — open OS file manager at path (select file if possible)."""
    wm: WorkspaceManager = request.app["wm"]
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


# ---------------------------------------------------------------- 认证 (/auth/*)
#
# 这些路由只在云端地址非空时注册 (见 register_desktop_routes)。为空时整套认证不加载, 现有本地
# 单用户流程零回归 —— 这是本期改动能安全落地的前提。
#
# Gateway 侧刻意只做**转发 + 本机凭证管理**: 不持任何供应商密钥 (安装包里放阿里云
# AK/SK 或 Resend key 等于公开发布), 不做授权判定 (用户本人即机器管理员, 客户端侧
# 校验可被绕过)。发码与鉴权都在云端。


def _auth(request: web.Request) -> AuthManager:
    return request.app["authm"]


def _auth_reply(status: int, body: dict[str, Any]) -> web.Response:
    """把云端响应原样转给 SPA。

    ``status == 0`` 表示云端不可达 —— 转成 502, 而不是把 0 当 HTTP 状态码
    (aiohttp 会抛), 也不掩饰成 200。
    """
    if status == 0:
        return _json(body, status=502)
    return _json(body, status=status)


async def _auth_status(request: web.Request) -> web.Response:
    """当前登录态。SPA 据此决定显示登录引导还是身份信息; 不含 token。

    顺手把连接焐热: SPA 挂载登录面板时必然探这个端点, 是最自然的预热时机 ——
    因此前端一行都不用改。它本身只读内存、不打云端, 而 ``nudge_warm`` 只是往
    task group 里塞个任务就返回, 所以加上预热也不会让这个响应变慢。
    """
    authm = _auth(request)
    await authm.nudge_warm()
    return _json(authm.status())


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


async def _refresh_free_models(request: web.Request) -> None:
    """登录态变了, 让免费模型的 socket 重新取一次 token。

    ** 为什么要显式做 **: 交给 ``Ai`` 的 key 在 socket 构造时就定了, 而
    ``AiInfo.api_key`` 里存的是哨兵 —— 去重键看不见 token 变化, 不会自然重建。
    不做的话: 换账号登录后仍拿旧 token (已被云端吊销) 去请求, 一路 401;
    登出后仍能继续用, 更糟。

    只重建、不删除: 模型列表与 Session 绑定都不动, 用户看不到任何抖动。
    """
    authm: AuthManager = request.app["authm"]
    aim: AIManager = request.app["aim"]
    await aim.refresh_where(lambda info: is_cloud_free_model(info.api_key, info.base_url, authm.endpoint))


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
    # 老用户在这一步就拿到了正式 token; 新用户要走 /complete, 那边也刷。
    if status == 200:
        await _refresh_free_models(request)
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
    if status == 200:
        await _refresh_free_models(request)
    return _auth_reply(status, data)


async def _auth_me(request: web.Request) -> web.Response:
    status, data = await _auth(request).me()
    return _auth_reply(status, data)


async def _auth_logout(request: web.Request) -> web.Response:
    status, data = await _auth(request).logout()
    # 无条件刷: 云端不可达时本机凭证也已清掉 (logout_local), socket 必须跟着走,
    # 否则登出后免费模型还能继续用。
    await _refresh_free_models(request)
    return _auth_reply(status, data)


async def _auth_devices(request: web.Request) -> web.Response:
    """列出已登录设备。"""
    status, data = await _auth(request).list_devices()
    return _auth_reply(status, data)


async def _auth_revoke_device(request: web.Request) -> web.Response:
    """踢掉某台设备, 该设备下次请求即 401。"""
    status, data = await _auth(request).revoke_device(request.match_info.get("device_id", ""))
    return _auth_reply(status, data)


async def register_desktop_routes(
    app: web.Application,
    *,
    favicon_path: str | None = None,
    app_name: str = DEFAULT_APP_NAME,
    attention: AttentionHub | None = None,
    authm: AuthManager | None = None,
) -> web.Application:
    """ToC: SPA 静态资源 + 托盘注意力 + UI 偏好 + 本机目录浏览 + 登录。

    这些背后的 ``AttentionHub`` / ``UIPrefs`` / ``WorkspaceManager`` / ``AuthManager``
    都认识桌面概念 (托盘、Windows 盘符、本机凭证), 飞书容器里一个都用不上。

    ``appdata`` 不再单独收: ``UIPrefs`` 从骨架已经记下的 ``app["appdata"]`` 建 ——
    prefs 是个普通文件, 没有生命周期要谁来持有, 也没有值得调用方注入或伪造的东西。
    """
    app["wm"] = WorkspaceManager()
    app["favicon_path"] = favicon_path
    app["app_name"] = app_name
    app["attention"] = attention if attention is not None else AttentionHub()
    app["uiprefs"] = await UIPrefs.from_appdata(str(app.get("appdata") or ""))
    app["openapi_desktop"] = True

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

    app.router.add_post("/ui/attention", _request_attention)
    app.router.add_get("/ui/prefs/survey", _get_survey_pref)
    app.router.add_post("/ui/prefs/survey", _set_survey_pref)
    app.router.add_get("/workspace/cwd", _get_cwd)
    app.router.add_get("/workspace/places", _list_workspace_places)
    app.router.add_get("/workspace/browse", _browse_workspace)
    app.router.add_get("/workspace/file", _read_workspace_file)
    app.router.add_post("/workspace/reveal", _reveal_workspace_path)

    # 认证路由: 只在配了云端地址时才注册。authm 为 None 时**一条都不注册**,
    # 现有本地单用户流程零回归。
    if authm is not None:
        app["authm"] = authm
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
