"""ToB 路由装配 —— ``register_feishu_routes()`` 与 ``/feishu/*`` ``/oauth/*`` 的 handler。

A7: 与 ``desktop/_routes.py`` 同一个原因搬过来 —— 装配函数留在 ``gateway/server.py`` 时,
骨架为了给它备料必须 ``from psi_agent.gateway.feishu._feishu_manager import FeishuManager``,
于是「骨架不认识产品线」这条只靠纪律维持。现在骨架对本包一无所知。

``/oauth/*`` 两条也在这里: 取件方(实测)全在 ``agents/feishu/tools/`` 一侧, ToC 的登录走
手机号 + 验证码不经过 OAuth 跳转 —— 理由详见 ``_oauth_manager`` 模块头。

但它们由**独立的** ``register_oauth_routes()`` 注册, 不跟着 ``--gateway`` 走: 归属
(代码住哪)与可达性(哪个进程有这两条)是两件事。回调地址 ``PSI_OAUTH_CALLBACK_BASE``
要提前登记到第三方应用后台, 一个进程组合少贴这两条, 表现是用户点完授权拿到 404 ——
而不是某个功能没开。``register_feishu_routes()`` 在原位置调它, 只挂 ToC 的进程由
``Gateway.run`` 自己调, 于是任何组合下都在。

``_json`` / ``_error`` 从骨架 import: 方向是产品 → 骨架, 正是允许的那一向。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web
from loguru import logger

from psi_agent.gateway.feishu._auth import (
    AuthError,
    FeishuAuth,
    Identity,
    dev_open_id,
    warn_if_dev_bypass_enabled,
)
from psi_agent.gateway.feishu._feishu_manager import FeishuManager
from psi_agent.gateway.feishu._identity import owns_session, visible_sessions
from psi_agent.gateway.feishu._oauth_manager import OAuthRelay
from psi_agent.gateway.server import _error, _json, _read_json, _session_data
from psi_agent.runtime._history_manager import HistoryManager
from psi_agent.runtime._scheduler_manager import SchedulerManager
from psi_agent.runtime._session_manager import SessionInfo, SessionManager
from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager


async def _feishu_route(request: web.Request) -> web.Response:
    """幂等地把一次飞书会话路由到其 Session, 首次见到时按需 spawn。

    body: ``{open_id, chat_id?, chat_type?, ai_id?, workspace?}`` →
    ``201 {open_id, chat_id, session_id, channel_socket, external}``。群聊 (``chat_type`` 为
    group/topic 且 ``chat_id`` 非空) 整群共用一个 Session, 其余按 ``open_id`` 一人一个。channel
    拿回 ``channel_socket`` 连接即得对应会话; ``external`` 为真表示该 Session 跑在**别的容器**里,
    channel 据此不再下载附件到本机 (那边看不见), 改为透传 file_key。
    """
    fm: FeishuManager = request.app["fm"]
    schedm: SchedulerManager = request.app["schedm"]
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
        external = fm.is_external(open_id, chat_id=chat_id, chat_type=chat_type)
        # Schedules under this session's workspace belong to its dedicated scheduler
        # Session, not to the user/group session.
        #
        # 外部容器托管的会话本进程没有 Session, ``get_workspace`` 会抛 LookupError (转 404) ——
        # 它的定时任务由那个容器自己加载, 这里无事可做, 故跳过。历史上这里能跑通只是因为
        # 迁移前留下了一个同名本地 Session 兜住了查询; 那个残留一旦被清掉, 路由就会 404。
        sm: SessionManager = request.app["sm"]
        if not external:
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
                # channel 据此决定附件是自己下载还是透传 file_key 交给对端容器下载。
                "external": external,
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
    fm: FeishuManager = request.app["fm"]
    return _json([asdict(r) for r in fm.list_routes()])


SID_COOKIE = "psi_feishu_sid"
"""登录态 cookie 名。``HttpOnly`` 是要点: 页面脚本读不到它, XSS 也偷不走登录态。"""


def current_identity(request: web.Request) -> Identity | None:
    """当前请求的身份, 未登录返回 None。

    唯一来源是 ``HttpOnly`` cookie 里的 sid —— **不读 body/query 里的 open_id**。
    前端能伪造任何字段, 但伪造不出一个签发过的高熵 sid。会话过滤路由 (见下) 全部
    经由本函数取身份, 于是「谁在问」只有一个判据。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    return auth.lookup(request.cookies.get(SID_COOKIE, ""))


async def _auth_feishu(request: web.Request) -> web.Response:
    """``POST /feishu/auth/login`` —— body ``{code}`` → ``{open_id, name}`` + 登录 cookie。

    **body 里的 ``open_id`` 一律忽略**: 身份只能是 ``code`` 换回来的。前端传了也不看,
    这是本端点的安全前提。

    ``PSI_FEISHU_DEV_OPEN_ID`` 设了才有开发旁路, 且每次打 WARNING。默认不设置 → 无 code
    就是 400。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    body = await _read_json(request)
    code = ""
    if isinstance(body, dict):
        code = str(body.get("code") or "")

    if not code:
        bypass = dev_open_id()
        if bypass:
            # **每次旁路登录都留一条**, 与启动期那条 (``warn_if_dev_bypass_enabled``) 并存,
            # 是有意的重复: 启动那条只说明「开机时开关是开的」, 这条才是**实际被用了**的痕迹
            # —— 谁、什么时候借旁路进来的。生产上旁路默认关着, 这行一次都不会打, 所以「刷日志」
            # 的代价在生产等于零; 而万一被误开, 刷出来的量正是要的信号, 不是噪音。
            # ``dev_open_id()`` 现在是纯读, 不再替调用方打日志, 故这里显式打。
            logger.warning(
                "Feishu login via DEV BYPASS as {} -- not a real Feishu identity",
                bypass,
            )
            return _issue_login(
                Identity(open_id=bypass, name=bypass, via_dev_bypass=True),
                auth,
            )
        return _error("missing code", status=400)

    try:
        identity = await auth.identity_from_code(code)
    except AuthError as e:
        # 伪造/过期 code, 或 Gateway 未配凭证 —— 都是 4xx, 不是 500。
        logger.info(f"Feishu login rejected: {e}")
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error during Feishu login: {e!r}")
        return _error("login failed", status=500)
    return _issue_login(identity, auth)


def _issue_login(identity: Identity, auth: FeishuAuth) -> web.Response:
    """签发登录 cookie 并回身份。

    ``auth`` 必填而非可选: 两个调用点 (正常登录与开发旁路) 都必须签 cookie, 漏签的表现
    是登录看着成功、下一秒 ``/feishu/auth/me`` 401 —— 可选参数只会让这种漏法静默通过。
    """
    resp = _json(_identity_payload(identity))
    resp.set_cookie(
        SID_COOKIE,
        auth.issue(identity),
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return resp


def _identity_payload(identity: Identity) -> dict[str, Any]:
    """身份的线上表示。``login`` 与 ``me`` 共用一个形状。

    ``via_dev_bypass`` 只在为真时出现: 生产响应里因此**没有**这个字段, 前端 `!!data
    .via_dev_bypass` 天然是 false。合成一个函数是因为两个端点必须给出同一形状 —— 刷新页面
    走的是 ``me``, 只有 ``login`` 带这个字段的话, 告警条会在刷新后消失。
    """
    data: dict[str, Any] = {"open_id": identity.open_id, "name": identity.name}
    if identity.via_dev_bypass:
        data["via_dev_bypass"] = True
    return data


async def _auth_me(request: web.Request) -> web.Response:
    identity = current_identity(request)
    if identity is None:
        return _error("not logged in", status=401)
    return _json(_identity_payload(identity))


async def _auth_logout(request: web.Request) -> web.Response:
    auth: FeishuAuth = request.app["feishu_auth"]
    auth.revoke(request.cookies.get(SID_COOKIE, ""))
    resp = _json({"status": "ok"})
    resp.del_cookie(SID_COOKIE, path="/")
    return resp


async def _feishu_app_id(request: web.Request) -> web.Response:
    """前端免登要的 appID —— **只给 app_id, 永不给 app_secret**。

    前端因此不必写死 appID (PR 755 把它连同一个真实 open_id 一起硬编码在前端, 上云后
    所有访问者都变成同一个人)。未配置时返回空串而非 404: 前端据此显示「未配置免登」
    这条可读的提示, 而不是撞一个语义不明的 404。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    return _json({"app_id": auth.app_id})


async def _feishu_defaults(request: web.Request) -> web.Response:
    """``GET /feishu/defaults`` —— 网页应用建会话该挂哪个 AI: ``{ai_id}``, 就一个字段。

    **值是 Gateway 启动时的 ``--feishu-ai-id``**, 与机器人侧 ``FeishuManager._ai_id``
    同一个来源(下面那行 ``fm.default_ai_id``), 于是「网页应用和机器人用不同模型」在结构上
    不可能发生。前端原先自己打 ``GET /ais`` 取 ``ais[0]``: 生产上恰好只有一条 AI 所以看着
    没错, 而 appdata 里存了多条时数组顺序无保证, 网页应用会**静默**用上另一个模型。判据
    因此必须由后端给, 不是前端挑。

    **只下发 id**: ``api_key``/``base_url``/``provider``/``model`` 一个都不给。前端不需要
    它们(建会话只传 ``backend_id``), 而下发即等于把部署者的凭证摊给每个 B 端访问者。

    未配置时回空串而非 404 —— 与 ``/feishu/app-id`` 同一个取舍: 前端据此显示「部署没配 AI」
    这条可读的提示, 而不是撞一个语义不明的 404。空串是**正确**的部署态表达, 不是错误。

    不做鉴权: 内容是部署者自己定的一个实例 id, 不含凭证也不含任何用户数据, 与
    ``/feishu/app-id`` 同级。
    """
    fm: FeishuManager = request.app["fm"]
    return _json({"ai_id": fm.default_ai_id})


def _require_identity(request: web.Request) -> Identity:
    """取当前身份, 未登录抛 ``PermissionError`` (由各 handler 映射成 401)。

    **默认拒绝**是本组路由与骨架 ``/sessions`` 的关键差别: 骨架那条无身份即返回全量,
    于是漏传身份的后果是「泄漏」; 这里漏传的后果是 401。
    """
    identity = current_identity(request)
    if identity is None:
        raise PermissionError("not logged in")
    return identity


def _web_session_data(info: SessionInfo, *, from_im: bool) -> dict[str, Any]:
    """骨架的 ``_session_data`` 再加一个 ``from_im`` —— 前端据此打「来自飞书对话」角标。

    角标本身是产品决定二: IM 里那条 session 在网页里正常显示、可续聊, 但用户要能看出
    它与 IM 共通 (在里面发言 IM 侧也看得到)。
    """
    data = _session_data(info)
    data["from_im"] = from_im
    return data


async def _web_list_sessions(request: web.Request) -> web.Response:
    """``GET /feishu/sessions`` —— 只回当前身份可见的私聊会话。

    与骨架 ``GET /sessions`` 的关系: 骨架那条**语义一行不改**(ToC 的 spa-v2 在用), 本条
    是飞书链上单独包的一层。过滤在**服务端**做 —— PR 755 在浏览器里 filter, 那只是显示
    过滤, 谁都能直接打裸路由拿全量。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    bot_sid = fm.session_id_for(identity.open_id)
    rows = visible_sessions(identity.open_id, await sm.list_all(), fm)
    return _json([_web_session_data(r, from_im=r.id == bot_sid) for r in rows])


async def _web_create_session(request: web.Request) -> web.Response:
    """``POST /feishu/sessions`` —— 开一个**全新**会话: 新 uuid + 新 jsonl。

    两条产品决定都落在这里:

    * **不传 ``id``** 给 ``SessionManager.create`` → 它走 ``id or _new_uuid()`` 发新 uuid,
      于是历史落到一个**新的** ``{appdata}/histories/<uuid>.jsonl``。这正是「飞书机器人
      开不了新会话、上下文一直往同一个文件里长」的解法。
    * **workspace 由 ``fm.workspace_for(open_id)`` 派生** → 同一个人的多个会话落**同一个**
      目录 (决定一)。不这么做的话每开一个会话就多一个空目录、交付物散落。派生绝不在此
      处重拼: 私聊侧 ``-`` 转义漏掉会让 open_id 为 ``chat-oc_x`` 的人与群 ``oc_x`` 撞进
      同一个目录。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    schedm: SchedulerManager = request.app["schedm"]
    body = await _read_json(request) or {}
    backend_id = str(body.get("backend_id") or body.get("ai_id") or "")
    try:
        info = await sm.create(
            backend_type="ai",
            backend_id=backend_id,
            workspace=fm.workspace_for(identity.open_id),
            agent=str(body.get("agent") or ""),
        )
        await schedm.ensure(info.workspace, ai_id=info.backend_id, agent=info.agent)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    return _json(_web_session_data(info, from_im=False), status=201)


async def _web_get_history(request: web.Request) -> web.Response:
    """``GET /feishu/sessions/{id}/history`` —— 只给自己的会话。

    别人的/群聊的 → 403 而非内容; 不存在的 → 404。先查存在性再判归属: 反过来会让
    「不存在」与「不属于你」都返回 403, 前端分不出「会话被删了」和「越权」。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    hm: HistoryManager = request.app["hm"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    if not owns_session(identity.open_id, session_id, workspace, fm):
        return _error("forbidden", status=403)
    messages = await hm.get(workspace, session_id, appdata=str(request.app.get("appdata") or ""))
    return _json(messages)


async def _web_owned_ids(request: web.Request) -> set[str]:
    """当前身份可见的 session id 集合 —— titles/summaries 过滤共用。"""
    identity = _require_identity(request)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    return {s.id for s in visible_sessions(identity.open_id, await sm.list_all(), fm)}


async def _web_list_titles(request: web.Request) -> web.Response:
    """``GET /feishu/titles`` —— 标题表里只留自己会话的键。

    不过滤的话标题本身就是泄漏: 它是首句 prompt 派生的, 等于把别人问了什么摊出来。
    """
    try:
        owned = await _web_owned_ids(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    tm: TitleManager = request.app["tm"]
    return _json({k: v for k, v in tm.get_all().items() if k in owned})


async def _web_list_summaries(request: web.Request) -> web.Response:
    try:
        owned = await _web_owned_ids(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    sum_m: SummaryManager = request.app["sum_m"]
    return _json({k: v for k, v in sum_m.get_all().items() if k in owned})


def register_auth_routes(app: web.Application) -> web.Application:
    """把登录四条路由贴到 *app*。

    与 ``register_feishu_routes`` 分开是为了让单测能只贴这几条 —— 那边会建
    ``FeishuManager``, 要求一个真的 ``SessionManager`` 与 task group。
    """
    # **全部挂在 ``/feishu/`` 前缀下**, 一条都不占裸 ``/auth/*``: desktop 那条产品线
    # (``desktop/_routes.py``, ``authm`` 非 None 时) 已经注册了 ``GET /auth/me`` 与
    # ``POST /auth/logout``, 而 ``authm`` 默认就非 None (``resolve_endpoint()`` 有内置
    # 默认域名, 只有显式 ``PSI_AUTH_ENDPOINT=""`` 才关掉)。aiohttp 对同 path 的两次
    # ``add_get`` **不报错**, 各建一个 resource 并由先注册者胜出 —— 于是同进程装配下
    # (``gateway/__init__.py`` 先 desktop 后 feishu) 飞书这两条会永不执行: 实测有效
    # cookie 打 ``/auth/me`` 得 401 (desktop 不认飞书 sid) 而 ``/feishu/sessions`` 得
    # 200, 登出则走 desktop、飞书 sid 不被 revoke、cookie 不被清。加前缀是唯一让两条
    # 产品线都能在一个进程里活着的改法。
    app.router.add_post("/feishu/auth/login", _auth_feishu)
    app.router.add_get("/feishu/auth/me", _auth_me)
    app.router.add_post("/feishu/auth/logout", _auth_logout)
    app.router.add_get("/feishu/app-id", _feishu_app_id)
    return app


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
    relay: OAuthRelay = request.app["oauth"]
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
    relay: OAuthRelay = request.app["oauth"]
    state = request.query.get("state", "")
    if not state:
        return _error("state query parameter is required", status=400)
    pending = await relay.take(state)
    if pending is None:
        return _error("no callback received for this state yet", status=404)
    if pending.error:
        return _json({"state": state, "error": pending.error}, status=200)
    return _json({"state": state, "code": pending.code}, status=200)


def register_oauth_routes(app: web.Application) -> web.Application:
    """``/oauth/callback`` + ``/oauth/code`` 与它们共用的 ``OAuthRelay`` 信箱。

    **与挂了哪些 gateway 正交, 每种 ``--gateway`` 组合都要贴。** 单独成函数正是为此: 只挂
    ToC 的进程也得有这两条, 否则 ``PSI_OAUTH_CALLBACK_BASE`` 指过来的授权回调落到 404 ——
    那个地址登记在第三方应用后台, 不随本进程挂了哪些 gateway 而变。

    代码仍住 ``feishu/``, 按的是存在性判据: 取件方(实测)全在 ``agents/feishu/tools/``。
    归属与可达性是两件事, 后者由调用点保证 (``register_feishu_routes`` 或 ``Gateway.run``,
    恰好一处调到)。
    """
    app["oauth"] = OAuthRelay()
    app["openapi_oauth"] = True
    app.router.add_get("/oauth/callback", _oauth_callback)
    app.router.add_get("/oauth/code", _oauth_take_code)
    return app


def register_feishu_routes(
    app: web.Application,
    *,
    feishu_ai_id: str = "",
    feishu_workspace_root: str = "",
    feishu_app_id: str = "",
    feishu_app_secret: str = "",
) -> web.Application:
    """ToB: 飞书会话 → Session 的路由表。

    ``FeishuManager`` 复用骨架里的 ``SessionManager``, 但它自己认识飞书 (open_id /
    chat_id / 跨容器会话), 所以建在这里而不是骨架里 —— 桌面端容器不再无条件建它。

    **不碰 ``app["schedm"]``**: 原 ``create_app`` 里 ``scheduler_ai_id or feishu_ai_id``
    那个回落已经在唯一的生产调用点做掉了 (``Gateway.run`` 建 ``SchedulerManager`` 时,
    见 ``gateway/__init__.py``)。调度 Session 由骨架持有, 让产品层回头改它的私有字段
    等于把一个已建好对象的配置权分给两处。
    """
    sm: SessionManager = app["sm"]
    app["fm"] = FeishuManager(_sm=sm, _ai_id=feishu_ai_id, _workspace_root=feishu_workspace_root)
    app["openapi_feishu"] = True
    # 网页应用免登。**Gateway 从此持有 app_secret** —— 与 ``_oauth_manager`` 模块头那句
    # 「Gateway 侧刻意不碰 token 交换: 不知道 app_secret」是一次有意的变更, 不是疏漏:
    # 免登必须由后端拿 code 去换 token, 换的动作只能发生在知道 secret 的一侧, 而这一侧
    # 必须是服务端 (放前端等于公开 secret)。OAuthRelay 那条路径**照旧不碰 token**,
    # 两者互不影响。
    app["feishu_auth"] = FeishuAuth(app_id=feishu_app_id, app_secret=feishu_app_secret)
    # 开发旁路开着就在**启动期**喊一声。挂在这里的理由: 本函数是「装配飞书这条线」唯一的
    # 入口, 于是这条告警的可达性与旁路的可达性是同一个条件 —— 不挂飞书的进程压根没有
    # ``/feishu/auth/login``, 也就不该报旁路。
    #
    # 页面上那条常驻告警条已撤 (开发者启动时看见就够, 不必占每个用户一条通栏), 撤掉之后
    # 这里就是开发者唯一的提示。**先前根本没有启动期提示**: 唯一的痕迹是每次登录那条
    # WARNING, 所以「只删前端」会让旁路在没人登录前完全不可见。
    warn_if_dev_bypass_enabled()
    register_auth_routes(app)
    app.router.add_post("/feishu/route", _feishu_route)
    app.router.add_get("/feishu/routes", _list_feishu_routes)
    # 网页应用的缺省 AI。**贴在这里而不是 register_auth_routes 里**: 它读 ``app["fm"]``,
    # 而那个函数刻意不建 FeishuManager (单测只贴登录四条)。挂在那边的表现是单测里
    # ``app["fm"]`` KeyError → 500。
    app.router.add_get("/feishu/defaults", _feishu_defaults)
    # 按身份过滤的会话一族。**骨架 ``/sessions`` 一族不动** —— ToC 的 spa-v2 用的是那批,
    # 改它的语义会波及一面不相干的 gateway。这里是飞书链上单独的一层, 默认拒绝(401)。
    app.router.add_get("/feishu/sessions", _web_list_sessions)
    app.router.add_post("/feishu/sessions", _web_create_session)
    app.router.add_get("/feishu/sessions/{session_id}/history", _web_get_history)
    app.router.add_get("/feishu/titles", _web_list_titles)
    app.router.add_get("/feishu/summaries", _web_list_summaries)
    # ``/oauth/*`` 归本包(取件方全在 ToB 一侧), 但注册与 ``--gateway`` 解耦 —— 见
    # ``register_oauth_routes``。在此处调用是为了让两面全挂时的注册顺序与拆分前逐条不变;
    # 幂等由调用方保证 (``Gateway.run`` 只在不挂飞书时自己调)。
    register_oauth_routes(app)

    # ToB 前端的静态挂载点 —— 写法参照 ToC 侧两个 ``add_static``, 但存在性判断用同步的
    # ``pathlib``: 本函数是 ``def`` 而非 ``async def``, 改成协程要动 4 个调用点, 而这里
    # 要的只是「启动时目录在不在」。前缀与 ``feishu-web/vite.config.ts`` 的 ``base``
    # 是同一个字面量, 改一边忘另一边会静默 404 (``dist/`` 不存在时连 static 都不注册)。
    #
    # A7: 目录从**本模块**的 ``__file__`` 推 —— 装配函数搬进本包后不必再 import 包对象
    # 取 ``feishu_pkg.__file__`` (那一圈原是因为调用方住在骨架里)。
    feishu_web_dist = Path(__file__).parent / "feishu-web" / "dist"
    if feishu_web_dist.is_dir():
        logger.info(f"Feishu web enabled, serving {feishu_web_dist}")
        app.router.add_static("/feishu-web/", str(feishu_web_dist), show_index=False)
    else:
        logger.info(f"Feishu web dist absent ({feishu_web_dist}), static mount skipped")
    return app
