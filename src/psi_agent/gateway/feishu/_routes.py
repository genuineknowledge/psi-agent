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

import contextlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anyio
from aiohttp import web
from loguru import logger

from psi_agent import _private_space
from psi_agent._appdata import appdata_history_path, appdata_uploads_path, resolve_appdata_root
from psi_agent._service_auth import verify as verify_service_request
from psi_agent.gateway.feishu._auth import (
    AuthError,
    FeishuAuth,
    Identity,
    dev_open_id,
    warn_if_dev_bypass_enabled,
)
from psi_agent.gateway.feishu._feishu_manager import FeishuManager
from psi_agent.gateway.feishu._identity import is_org_session, owns_session, visible_sessions
from psi_agent.gateway.feishu._jsapi import FeishuJsapiSigner, JsapiError
from psi_agent.gateway.feishu._oauth_manager import OAuthRelay
from psi_agent.gateway.feishu._stats import current_month, month_bounds, monthly_run_stats, stats_tz
from psi_agent.gateway.server import (
    _delete_session,
    _error,
    _json,
    _read_json,
    _serve_chat_sse,
    _session_ai_socket,
    _session_data,
)
from psi_agent.runtime._history_manager import HistoryManager
from psi_agent.runtime._scheduler_manager import SchedulerManager
from psi_agent.runtime._session_manager import SessionInfo, SessionManager
from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager
from psi_agent.runtime._todo_manager import TodoManager


async def _require_service(request: web.Request) -> None:
    """判断「这是不是 channel 进程发的」, 不是就抛 ``PermissionError`` (handler 映射成 401)。

    ``/feishu/route`` 一族是**进程间**接口 (channel → gateway): 调用方没有、也不该有一份
    用户登录态, 所以判据不是 cookie 而是共享密钥的 HMAC, 见
    :mod:`psi_agent._service_auth` 模块头。

    **默认拒绝**: ``app_secret`` 没配 (空串) 也算验不过 —— 空密钥下 HMAC 退化成人人可算的
    常量, 「这个部署没配好凭证」必须表现成 401, 不能表现成放行。

    读体在这里发生 (``request.read()`` 会缓存, 后面 ``request.json()`` 照旧能读), 因为
    签名覆盖 body 摘要: 只看 path 的话, spawn 哪个 open_id 就能被中途改写。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    body = await request.read()
    if verify_service_request(
        auth.app_secret,
        method=request.method,
        path=request.path_qs,
        body=body,
        headers=request.headers,
    ):
        return
    # 不记签名本身 (等同凭据): 记路径与两种「像没签」的形态, 够定位「channel 没带上」还是
    # 「两边 secret 不一致」。
    logger.warning(
        f"[feishu] 服务间路由拒绝: 签名无效 path={request.path_qs} "
        f"has_signature={bool(request.headers.get('X-PSI-Service-Signature'))} "
        f"has_timestamp={bool(request.headers.get('X-PSI-Service-Timestamp'))}"
    )
    raise PermissionError("not authorized")


async def _feishu_route(request: web.Request) -> web.Response:
    """幂等地把一次飞书会话路由到其 Session, 首次见到时按需 spawn。

    body: ``{open_id, chat_id?, chat_type?, ai_id?, workspace?}`` →
    ``201 {open_id, chat_id, session_id, channel_socket, external}``。群聊 (``chat_type`` 为
    group/topic 且 ``chat_id`` 非空) 整群共用一个 Session, 其余按 ``open_id`` 一人一个。channel
    拿回 ``channel_socket`` 连接即得对应会话; ``external`` 为真表示该 Session 跑在**别的容器**里,
    channel 据此不再下载附件到本机 (那边看不见), 改为透传 file_key。

    **只服务 channel 进程**: 未带有效签名一律 401 (见 :func:`_require_service`)。它按需
    spawn 会话、并回内部管道路径, 是这套接口里权限最高的两条之一 —— 在它没有鉴权的那些
    日子里, 任何能打到 gateway 端口的东西都能凭一句 ``{"open_id": …}`` 建出会话。
    """
    try:
        await _require_service(request)
    except PermissionError as e:
        return _error(str(e), status=401)
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
    """``GET /feishu/routes`` —— 全部「飞书会话 → Session」映射, 供观测。

    **与 ``POST /feishu/route`` 同一道判据** (见 :func:`_require_service`): 它回的是**所有人**
    的路由表 —— 谁在用这个部署、各自的 session id 与 open_id/chat_id。读侧比写侧轻, 但泄漏的
    是同一份东西, 所以不放行匿名读。channel 一次都不打这条 (它只用 POST), 于是它天然是
    「运维/排查」接口, 不该对浏览器可达。
    """
    try:
        await _require_service(request)
    except PermissionError as e:
        return _error(str(e), status=401)
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
    # 生产 HTTPS 必须让 cookie 只随 TLS 传输; 本地 HTTP 默认关闭, 部署时显式开。
    secure = (os.environ.get("PSI_FEISHU_COOKIE_SECURE", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    resp.set_cookie(
        SID_COOKIE,
        auth.issue(identity),
        httponly=True,
        samesite="Lax",
        secure=secure,
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


async def _feishu_jsapi_config(request: web.Request) -> web.Response:
    """``GET /feishu/jsapi/config?url=...`` -> ``tt.config`` 的签名参数。

    ``url`` 由前端传 ``location.href.split('#')[0]``, 后端只拿它拼签名, 不下发
    ticket 或 App Secret。未配置凭证、URL 非法或上游失败都回 4xx, 不让前端误以为
    签名可用。
    """
    signer: FeishuJsapiSigner = request.app["feishu_jsapi"]
    url = request.query.get("url", "")
    if not url.strip():
        return _error("url query parameter is required", status=400)
    try:
        config = await signer.config_for_url(url)
    except JsapiError as e:
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error while signing Feishu JSAPI config: {e!r}")
        return _error("jsapi config failed", status=500)
    return _json(config)


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


class _AccessDeniedError(Exception):
    """会话级路由的准入失败 —— ``status`` 就是该回的 HTTP 码, 由调用方 ``_error`` 出去。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


#: 组织共享会话的只读拒绝文案。
#:
#: 给**用户看**的: 它会被前端原样显示在对话底部。所以是中文、说的是「你能做什么」, 而不是
#: 一句 ``org session is read-only`` 那种只有实现者看得懂的英文 —— 实测有人把它当成了
#: 「新建的对话坏了」, 而真相是那条会话本来就不接受消息。
ORG_SESSION_READ_ONLY = "这是组织共享任务, 只能查看历史, 不能在里面发消息。想继续做, 请点「新建任务」开一个自己的会话。"


def _authorize_session(request: web.Request, *, write: bool = False) -> tuple[Identity, str, str]:
    """路径参数版: 会话 id 取自 ``{session_id}``。判定体在 :func:`_authorize_owned`。"""
    return _authorize_owned(request, request.match_info["session_id"], write=write)


def _authorize_owned(request: web.Request, session_id: str, *, write: bool = False) -> tuple[Identity, str, str]:
    """把「谁在问、问的是哪条会话、归不归他」一次判完 → ``(identity, session_id, workspace)``。

    失败抛 :class:`_AccessDeniedError`, 每个 handler 只需三行把它映射成响应。

    **三段顺序不能变** (与原先 ``_web_get_history`` 逐字一致): 身份 401 → 存在性 404 →
    归属 403。反过来先判归属的话, 「不存在」与「不是你的」会糊成同一个状态, 前端分不出
    「会话被删了」和「越权」。

    ``write=True`` 额外拒绝组织共享调度会话 (只读): 任何人可读它的历史, 但任何人不得驱动
    其中的工具。这条规则原先只写在 ``_web_chat`` 里 —— 抽出来是为了让**后加的**会话级
    读写路由不可能漏掉它。

    ``session_id`` 由调用方给而不是固定从 ``match_info`` 取: 标题那两条的 id 在 **body**
    里(前端那侧的形状就是 ``{id, title}``), 而判定必须与路径参数版**完全同一份** ——
    谁再写一遍谁就可能漏掉 ``write`` 那一步。

    **拒绝一律记一条 WARNING**, 带上会话 id 与原因: 403 这条路径不产生任何其它日志, 用户
    截图里只有一句错误文案时, 服务端这边必须有东西能对上号(实测踩过 —— 「新建对话报 org
    session is read-only」查了半天才定位到是哪条会话)。**不记 cookie / 身份细节**: 记的是
    open_id 与 session id, 与访问日志同级。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        logger.warning(f"[feishu] 会话级路由拒绝: 未登录 path={request.path}")
        raise _AccessDeniedError(401, str(e)) from e
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        logger.warning(f"[feishu] 会话级路由拒绝: 会话不存在 session={session_id!r} open_id={identity.open_id}")
        raise _AccessDeniedError(404, f"Session '{session_id}' not found") from None
    if write and is_org_session(session_id, workspace):
        logger.warning(
            f"[feishu] 会话级路由拒绝: 组织共享会话只读 "
            f"session={session_id!r} open_id={identity.open_id} path={request.path}"
        )
        raise _AccessDeniedError(403, ORG_SESSION_READ_ONLY)
    if not owns_session(identity.open_id, session_id, workspace, fm):
        logger.warning(
            f"[feishu] 会话级路由拒绝: 越权 session={session_id!r} open_id={identity.open_id} path={request.path}"
        )
        raise _AccessDeniedError(403, "forbidden")
    return identity, session_id, workspace


def _download_response(path: Path | str, *, filename: str = "") -> web.FileResponse:
    """以附件形式回一个文件。

    文件名**必须走 RFC 5987** (``filename*=UTF-8''…``): 交付物常是中文名 (「周会纪要.md」),
    直接塞进 ``filename=`` 会让浏览器拿到乱码、有的干脆丢掉这个头, 用户下到的文件名就成了
    路由里的那串。``ASCII`` 回退名给一个安全的兜底。
    """
    real = Path(path)
    name = filename or real.name
    ascii_fallback = name.encode("ascii", "ignore").decode("ascii") or "download"
    disposition = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
    return web.FileResponse(real, headers={"Content-Disposition": disposition})


def _web_session_data(info: SessionInfo, *, from_im: bool) -> dict[str, Any]:
    """骨架的 ``_session_data`` 再加 ``from_im`` / ``read_only`` 两个前端判据。

    * ``from_im``: IM 里那条 session 在网页里正常显示、可续聊, 但用户要能看出它与 IM
      共通(在里面发言 IM 侧也看得到)。
    * ``read_only``: 组织共享的调度会话。它对所有登录用户**只读**(历史能看、消息不能发)。
      2026-09-16 起这类会话**不进列表**(见 ``_web_list_sessions``), 所以这个标记在日常路径上
      不会出现; 留着是因为它是「这条会话不能写」的唯一机器可读判据 —— 直打
      ``/feishu/sessions/{id}/…`` 的调用方(深链、将来的入口)靠它把输入框关掉, 而不是让用户
      打完字才吃一个 403。真正的闸在 ``_authorize_session(write=True)``, 与这个标记同源
      (都走 ``is_org_session``)。
    """
    data = _session_data(info)
    data["from_im"] = from_im
    data["read_only"] = is_org_session(info.id, info.workspace or "")
    return data


async def _web_list_sessions(request: web.Request) -> web.Response:
    """``GET /feishu/sessions`` —— 只回当前身份**能干活**的会话(自己的私聊会话)。

    与骨架 ``GET /sessions`` 的关系: 骨架那条**语义一行不改**(ToC 的 spa-v2 在用), 本条
    是飞书链上单独包的一层。过滤在**服务端**做 —— PR 755 在浏览器里 filter, 那只是显示
    过滤, 谁都能直接打裸路由拿全量。

    **组织共享的调度会话不进这个列表**(2026-09-16 产品决定, 实测反馈「感觉没有什么用」):
    它在网页应用里只能只读查看 —— 不能发消息、不能删、没有标题(显示成「未命名任务」),
    只能永远停在「待开始/0%」, 却和用户自己的任务混在同一排里。组织级任务的产出由机器人以
    卡片发到飞书 IM, 那里才是它的入口。

    **隐藏 ≠ 放开**: 归属判定一字未改, 直打 ``/feishu/sessions/{id}/…`` 仍然是「历史可读、
    写入 403」(``is_org_session`` 那条闸还在 ``_authorize_session`` 里)。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    bot_sid = fm.session_id_for(identity.open_id)
    rows = visible_sessions(identity.open_id, await sm.list_all(include_scheduler=True), fm)
    # 组织共享的调度会话不进列表 —— 理由见 docstring。判定仍走 ``is_org_session`` 那一份,
    # 于是「列表里看不到」与「写不进去」用的是同一个判据, 不会各说各话。
    owned = [r for r in rows if not is_org_session(r.id, r.workspace or "")]
    return _json([_web_session_data(r, from_im=r.id == bot_sid) for r in owned])


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
    """``GET /feishu/sessions/{id}/history`` —— 只给自己的会话, 会议会话例外公开只读。

    别人的/群聊的 → 403 而非内容; 不存在的 → 404。三段判定抽到了 ``_authorize_session``
    (先存在性再归属: 反过来会让「不存在」与「不属于你」都返回 403)。
    """
    try:
        _identity, session_id, workspace = _authorize_session(request)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    hm: HistoryManager = request.app["hm"]
    messages = await hm.get(workspace, session_id, appdata=str(request.app.get("appdata") or ""))
    return _json(messages)


async def _web_chat(request: web.Request) -> web.StreamResponse:
    """``POST /feishu/sessions/{id}/chat`` —— 带鉴权的聊天流, 只许操作自己的会话。

    **为什么要有这一条**: 骨架的 ``POST /sessions/{id}/chat`` 一行身份校验都没有 (它在容器内
    回环服务本机, 那是它的合理用途)。而它是**能驱动 agent 执行工具**的那条 —— 跑 bash、读
    公司表格、往飞书发消息。把裸的那条放上公网等于任何知道一个 session id 的人都能让公司
    agent 干活, 且不问他是谁。所以网页应用改打这条对等物, 裸的那条**行为一字不改**。

    判定与 ``_web_get_history`` 共用 ``_authorize_session`` (``write=True``), 后者已经把
    「组织共享会话只读」一起判了 —— 从前那段只写在这个 handler 里, 后加的会话级写路由会漏。
    """
    try:
        _identity, session_id, _workspace = _authorize_session(request, write=True)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    return await _serve_chat_sse(request, session_id)


def _norm_path(value: Path | str) -> str:
    """比较用的路径规范形: 展开 ``~``、解析相对段、Windows 下归一大小写。

    与 ``_identity._same_path`` 同一个理由 —— 同一个文件在不同人手里可能写成
    ``/x/y.md`` / ``/x/./y.md`` / ``C:\\X\\Y.MD``, 直接比字符串会漏判。
    """
    p = Path(value).expanduser()
    with contextlib.suppress(OSError, RuntimeError):
        p = p.resolve(strict=False)
    return os.path.normcase(str(p))


def _resolve_deliverable(raw: str) -> Path | None:
    """``raw`` → 磁盘上真实存在的文件; 不存在 / 不是文件 → ``None``。

    刻意是**同步**函数: 调用方是 async handler, 而 anyio.Path 那一套在这里换不到什么 ——
    这是一次本地 stat, 与 ``_norm_path`` 同类。真要挪进线程就两处一起挪, 不做半套
    (ruff 的 ASYNC240 只盯 async 函数体里直接调 ``Path`` 方法, 所以判定逻辑收在这里)。
    """
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except OSError, RuntimeError:
        return None
    return resolved if resolved.is_file() else None


async def _recorded_uploads(appdata: str, session_id: str) -> set[str]:
    """``{appdata}/uploads/{sid}.jsonl`` 里登记过的入向文件 (规范化路径)。

    读不到 / 坏行一律跳过: 白名单的失败方向必须是「少放行」, 不能是抛错 —— 让一条坏行把
    整条下载路由变成 500, 等于把「登记簿坏了」升级成「这个会话的交付物全下不动」。
    """
    if not appdata:
        return set()
    try:
        text = await appdata_uploads_path(appdata, session_id).read_text(encoding="utf-8")
    except OSError:
        return set()
    out: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"[feishu] 上传登记簿有坏行, 已跳过 session={session_id!r}")
            continue
        path = row.get("path") if isinstance(row, dict) else None
        if isinstance(path, str) and path.strip():
            out.add(_norm_path(path))
    return out


async def _session_deliverable_paths(request: web.Request, session_id: str, workspace: str) -> set[str]:
    """该会话**服务端登记过的**交付物绝对路径集合(规范化后)。

    两个来源, 都是**服务端自己写下的记录**:

    * ``sends`` / ``files[].path`` —— history 行里 agent 交付的文件(宝箱那一侧);
    * ``{appdata}/uploads/{sid}.jsonl`` —— 本会话的**入向**文件, 由
      ``ChatManager._save_upload`` 在落盘那一刻追加(见 ``_appdata.appdata_uploads_path``)。

    **刻意不读 history 的 ``recvs``**(原先的第三个来源): 它是从**用户正文**里正则捞出来的
    ``[RECV:…]``, 于是白名单有了一个用户可控的输入 —— 往消息里打一句
    ``[RECV:C:\\Windows\\win.ini]``, 那条路径就进了白名单, 而下载那条路由的**唯一**闸门就是
    这份集合。用户能自己往里加路径的白名单不叫白名单。入向文件改由服务端登记之后, 判据变成
    「这个文件确实是本会话收下的」, 与正文里写了什么无关。

    代价说清楚: 修复前就已存在的会话, 其历史里那些 ``[RECV:]`` 不再进白名单 —— 那些附件
    (若还在磁盘上) 要重新上传一次才能从宝箱下载。**这是有意的**: 无法区分「channel 编码的
    上传」与「用户手打的标记」, 而后者正是要堵的口子。

    ``sends`` 侧保持原样(不另加「必须落在 workspace 下」的判定): agent 本来就拿得到文件系统
    (它的 ``read``/``bash`` 是任意路径), 由它声明交付的文件不构成新的越权面; 而 workspace
    之外的合法交付物(写到别的目录)会被这条判定误伤。真正可控的输入只有用户正文那一处。
    """
    hm: HistoryManager = request.app["hm"]
    appdata = str(request.app.get("appdata") or "")
    rows = await hm.get(workspace, session_id, appdata=appdata)
    out: set[str] = set()
    for row in rows:
        # ``row`` 是 dict[str, object], 所以每层都显式判类型 —— ``row.get(k) or []`` 那样的
        # 写法 ty 会报 not-iterable(object 未必可迭代), 而它是对的: 历史行的形状由
        # ``_history_manager`` 决定, 这里不该假定。
        entries = row.get("sends")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str) and entry.strip():
                    out.add(_norm_path(entry))
        files = row.get("files")
        if not isinstance(files, list):
            continue
        for entry in files:
            if isinstance(entry, dict):
                path = entry.get("path")
                if isinstance(path, str) and path.strip():
                    out.add(_norm_path(path))
    return out | await _recorded_uploads(appdata, session_id)


async def _web_todos(request: web.Request) -> web.Response:
    """``GET /feishu/sessions/{id}/todos`` —— 骨架那条的**带鉴权对等物**。

    为什么要有: 网页应用的「任务进度 / 执行步骤 / 当前阶段」全由这条驱动, 而骨架的
    ``/sessions/{id}/todos`` 一行鉴权都没有、又在反代白名单之外 —— 正式环境里它恒 404,
    表现是左侧任务上下文永远停在「待继续」、进度恒为 0。这里走 cookie 身份 + 归属校验,
    且落在 ``/feishu/sessions/`` 前缀下(该前缀已在白名单), **不需要动 oauth-proxy**。

    正文实现不复制: 依然是 ``todom.get()`` 那一份。
    """
    try:
        _identity, session_id, workspace = _authorize_session(request)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    todom: TodoManager = request.app["todom"]
    appdata = str(request.app.get("appdata") or "")
    return _json(await todom.get(workspace, session_id, appdata=appdata))


async def _web_todo_segments(request: web.Request) -> web.Response:
    """``GET /feishu/sessions/{id}/todo-segments`` —— 「历史子任务」列表(新的在前)。"""
    try:
        _identity, session_id, _workspace = _authorize_session(request)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    todom: TodoManager = request.app["todom"]
    appdata = str(request.app.get("appdata") or "")
    return _json(await todom.list_segments(session_id, appdata=appdata))


async def _web_todo_segment(request: web.Request) -> web.Response:
    """``GET /feishu/sessions/{id}/todo-segments/{segment_id}`` —— 单个分段(含 todos[])。"""
    try:
        _identity, session_id, _workspace = _authorize_session(request)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    todom: TodoManager = request.app["todom"]
    segment_id = request.match_info["segment_id"]
    appdata = str(request.app.get("appdata") or "")
    seg = await todom.get_segment(session_id, segment_id, appdata=appdata)
    if seg is None:
        return _error(f"Todo segment '{segment_id}' not found", status=404)
    return _json(seg)


async def _web_download_file(request: web.Request) -> web.StreamResponse:
    """``GET /feishu/sessions/{id}/files?path=`` —— 下载该会话的交付物(宝箱用)。

    ``path`` 由前端从历史恢复, 是绝对路径。边界是**``_session_deliverable_paths`` 那份
    白名单**: 只允许下载"这条会话自己收下 / 交付过的文件"。那份集合的两个来源都是服务端
    写下的记录 —— 用户正文说了不算, 理由见该函数。

    这里另有一道**私密区**判定(``_private_space.blocks_send``): channel 那条出向路径早就
    拒绝把别人的私密文件发进飞书, 网页宝箱走的是另一条路, 不判的话「channel 不发」就只是
    一半的守卫。判据与那道一字不差(同一个函数), 依据是请求身份, 本端点知道是谁在问。

    **刻意不额外要求"落在 workspace 下"**: 会话共享 workspace, 而交付物完全可能落在
    workspace 之外(agent 写到别的目录、用户上传的附件被下到 ``~/Downloads/.psi/``), 加这条
    会挡住正当下载; 而白名单已经把它限定在"本会话登记过/交付过的文件"上, 归属校验又把它
    限定在"本人的会话"上, 所以不必再叠一层会误伤的判定。

    刻意**不复用** desktop 面的 ``/workspace/file``: 那条只在挂载 desktop 时存在
    (云端 ``launch-gateway.sh`` 只挂 ``--gateway feishu``), 而且它无鉴权、按任意路径读。
    """
    try:
        identity, session_id, workspace = _authorize_session(request)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    raw = (request.query.get("path") or "").strip()
    if not raw:
        return _error("path query parameter is required", status=400)
    allowed = await _session_deliverable_paths(request, session_id, workspace)
    resolved = _resolve_deliverable(raw)
    if resolved is None:
        return _error("file not found", status=404)
    if _norm_path(resolved) not in allowed:
        return _error("not a deliverable of this session", status=403)
    if _private_space.blocks_send(resolved, identity.open_id):
        logger.warning(f"[feishu] 交付物下载拒绝: 私密区 session={session_id!r} open_id={identity.open_id}")
        return _error("not a deliverable of this session", status=403)
    return _download_response(resolved)


async def _web_export_history(request: web.Request) -> web.StreamResponse:
    """``GET /feishu/sessions/{id}/export`` —— 下载该会话的**原始** ``jsonl``。

    给磁盘上那份文件, 而不是 ``/history`` 那种投影过的显示行: 用户要的是「对话历史文件」,
    投影会丢掉工具调用参数、``thinking_ms`` 之类字段, 拿回去与原始记录对不上。

    文件名固定 ``{session_id}.jsonl`` —— 它既是会话 id 也是磁盘上的文件名, 全程不拿用户
    输入拼路径。``appdata_history_path`` 是 ``_history_manager`` 读同一份文件用的同一个助手,
    不自己拼 ``histories/`` 这层目录。
    """
    try:
        _identity, session_id, _workspace = _authorize_session(request)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    appdata = str(request.app.get("appdata") or "") or await resolve_appdata_root()
    history = anyio.Path(appdata_history_path(appdata, session_id))
    if not await history.is_file():
        return _error("no history file for this session", status=404)
    return _download_response(str(history), filename=f"{session_id}.jsonl")


async def _web_set_title(request: web.Request) -> web.Response:
    """``POST /feishu/titles`` —— 改**自己**会话的标题。

    裸的 ``POST /titles`` 无鉴权且在白名单外, 云上恒 404 —— 表现是列表里那条会话永远是
    「未命名任务」, 用户也没法给它改名。这里把 id 从 body 里取出来先判归属, 再走
    ``tm.set`` 那一份(不复制实现)。

    ``write=True``: 组织共享的调度会话对所有人只读, 谁都不能给它改名。
    """
    body = await _read_json(request) or {}
    session_id = str(body.get("id") or "")
    title = str(body.get("title") or "")
    if not session_id or not title:
        return _error("id and title are required", status=400)
    try:
        _identity, session_id, _workspace = _authorize_owned(request, session_id, write=True)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    tm: TitleManager = request.app["tm"]
    await tm.set(session_id, title)
    return _json({"id": session_id, "title": title})


async def _web_generate_title(request: web.Request) -> web.Response:
    """``POST /feishu/titles/generate`` —— 用首轮问答派生的标题(带归属校验)。

    与 ``_web_set_title`` 同样是「先判归属再交给骨架那一份」, 区别是这里要在服务端跑一次
    模型, 所以**它是这一族里除了 chat 之外唯一会产生费用的路由**: 归属校验不是可选项。
    """
    body = await _read_json(request) or {}
    session_id = str(body.get("id") or "")
    if not session_id:
        return _error("id is required", status=400)
    try:
        _identity, session_id, _workspace = _authorize_owned(request, session_id, write=True)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    try:
        ai_socket = await _session_ai_socket(request, session_id)
    except LookupError as e:
        return _error(str(e), status=404)
    tm: TitleManager = request.app["tm"]
    title = await tm.generate(
        session_id,
        ai_socket,
        str(body.get("user_text") or ""),
        str(body.get("assistant_text") or ""),
    )
    if not title:
        logger.warning(f"Title generation returned no result for session {session_id!r}")
        return _error("Failed to generate title", status=500)
    return _json({"id": session_id, "title": title})


async def _web_delete_session(request: web.Request) -> web.Response:
    """``DELETE /feishu/sessions/{id}`` —— 删自己的会话(**硬闸**, 不只是前端隐藏按钮)。

    裸的 ``DELETE /sessions/{id}`` 在云上被白名单挡着, 网页应用的删除按钮点了没反应。
    这里补的是**带鉴权的对等物**, 正文交给骨架的 ``_delete_session``(会话/历史/todo/标题/
    摘要五处一起清) —— 不复制那份实现。

    比只读那一族多一条闸: **与飞书机器人共用的那条不许删**。它承载的是机器人那侧的同一份
    上下文, 删掉等于把 IM 里的对话一起扔掉, 而用户还会在 IM 里继续用它 —— 前端本来就把
    那条的删除按钮藏了, 但那只是显示层的闸, 直打接口照样能删。这里按会话 id 判(**不是**
    按前端传来的 from_im): 同一条会话在前端是不是"来自飞书对话"由后端算, 判据必须同源,
    否则改一个 body 字段就能绕过。
    """
    try:
        identity, session_id, _workspace = _authorize_session(request, write=True)
    except _AccessDeniedError as e:
        return _error(e.message, status=e.status)
    fm: FeishuManager = request.app["fm"]
    if session_id == fm.session_id_for(identity.open_id):
        return _error("the session shared with the Feishu bot cannot be deleted", status=403)
    return await _delete_session(request)


async def _web_owned_ids(request: web.Request) -> set[str]:
    """当前身份可见的 session id 集合 —— titles/summaries 过滤共用。"""
    identity = _require_identity(request)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    return {s.id for s in visible_sessions(identity.open_id, await sm.list_all(include_scheduler=True), fm)}


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


async def _web_monthly_stats(request: web.Request) -> web.Response:
    """``GET /feishu/stats/monthly[?month=YYYY-MM]`` —— 任务总览「本月执行」那一格的取数。

    **为什么要有一条接口**: 那一格此前是前端对**每个会话**各打一次 ``/todo-segments`` 再自己
    数 —— 会话一多就是 N 次请求; 更糟的是它只看 todo 段, 于是 agent 直接回答/直接调工具的那些
    回合(不写 todo)全被算成「这个月没干活」, 而列表里它们的状态早就显示「已完成」了。同一屏
    两个数字互相打架。

    口径与取数都在 :mod:`psi_agent.gateway.feishu._stats` 里, 这里只做身份与参数两件事:

    * 身份: 未登录 401。**只统计自己可见的会话**(与 ``/feishu/sessions`` 同一份过滤 ——
      组织共享会话那边不进列表, 这里也不进, 否则两个数字的"人口"不一致)。
    * 参数: ``month`` 缺省 = 服务端当前自然月; 形状不对是 400 而不是回 0 —— 回 0 会把
      「参数写错了」伪装成「本月什么都没跑」。

    响应除 ``count`` / ``checklist`` / ``reply`` 外还带 ``sessions`` (人口 = 本人可见会话数) 与
    ``unlisted`` (有本月活动、却不在人口里、且能确认属于本人的会话数)。后两项是为了让
    「用户自己数 ``todos/`` 与这一格不一致」有处可查 —— 差异来自人口是会话注册表而不是磁盘
    数据文件, 详见 ``_stats`` 模块头。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    raw_month = (request.query.get("month") or "").strip()
    month = raw_month or current_month(tz=stats_tz())
    try:
        start, end = month_bounds(month, tz=stats_tz())
    except ValueError:
        return _error("month must be YYYY-MM", status=400)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    todom: TodoManager = request.app["todom"]
    rows = visible_sessions(identity.open_id, await sm.list_all(include_scheduler=True), fm)
    owned = [(r.id, r.workspace or "") for r in rows if not is_org_session(r.id, r.workspace or "")]
    stats = await monthly_run_stats(
        sessions=owned,
        appdata_root=str(request.app.get("appdata") or ""),
        todom=todom,
        start=start,
        end=end,
        fm=fm,
        open_id=identity.open_id,
    )
    return _json({"month": month, **stats})


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
    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>授权完成</title><style>"
    "html,body{{width:100%;height:100%;margin:0;padding:0;}}"
    "body{{display:grid;place-items:center;"
    "font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
    "background:linear-gradient(160deg,#f4f7fd,#e8eefb);}}"
    ".card{{background:#fff;border-radius:20px;box-shadow:0 14px 44px rgba(38,72,150,.14);"
    "padding:40px 52px;max-width:400px;width:100%;box-sizing:border-box;text-align:center;}}"
    ".icon{{font-size:52px;line-height:1;margin-bottom:12px;}}"
    "h1{{font-size:21px;margin:0 0 10px;color:#1c2b4a;}}"
    "p{{margin:0 0 26px;color:#5a6b8c;font-size:14.5px;line-height:1.75;}}"
    ".btn{{display:inline-block;padding:9px 26px;border:1px solid #d3daea;border-radius:999px;"
    "background:#fff;color:#5a6b8c;font-size:14px;cursor:pointer;text-decoration:none;}}"
    ".btn:hover{{background:#f2f5fc;color:#1c2b4a;}}"
    ".btn.primary{{background:#3370ff;border-color:#3370ff;color:#fff;}}"
    ".btn.primary:hover{{background:#275fe0;color:#fff;}}"
    "#hint{{display:none;margin-top:14px;color:#9aa7bd;font-size:12.5px;}}"
    "</style></head><body><div class='card'>"
    "<div class='icon'>{icon}</div><h1>{title}</h1><p>{note}</p>"
    "<button class='btn' onclick='closePage()'>✕ 关闭页面</button>"
    "{feishu_btn}"
    "<p id='hint'>浏览器未允许自动关闭, 请手动关闭本标签页后回到飞书。</p>"
    "</div>"
    "<script>function closePage(){{try{{window.close();}}catch(e){{}}"
    "setTimeout(function(){{document.getElementById('hint').style.display='block';}},400);}}</script>"
    "</body></html>"
)


def _oauth_chat_from_state(state: str) -> str:
    """从 ``<random>.oc_xxx`` 形态的 state 里取回 chat_id (授权发起时拼入的尾巴)。"""
    return state.split(".", 1)[1] if "." in state else ""


def _oauth_chat_btn(chat_id: str) -> str:
    """「回到飞书对话」按钮: applink 深链直接打开该会话, 让用户授权完就回到聊天。"""
    if not chat_id:
        return ""
    from urllib.parse import quote  # noqa: PLC0415

    href = f"https://applink.feishu.cn/client/chat/open?chatId={quote(chat_id, safe='')}"
    return f"<p style='margin:12px 0 0'><a class='btn primary' href='{href}'>回到飞书对话</a></p>"


def _oauth_html(title: str, note: str, status: int = 200, feishu_chat: str = "") -> web.Response:
    icon = "✅" if "成功" in title else "⚠️"
    return web.Response(
        text=_OAUTH_DONE_HTML.format(
            icon=icon,
            title=title,
            note=note,
            feishu_btn=_oauth_chat_btn(feishu_chat),
        ),
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
    chat = _oauth_chat_from_state(state)
    if error:
        return _oauth_html("授权未完成", "可以回到对话里重新发起授权。", status=400, feishu_chat=chat)
    return _oauth_html("授权成功", "授权已完成, 现在可以回到飞书继续对话了。", feishu_chat=chat)


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
    app["feishu_jsapi"] = FeishuJsapiSigner(app_id=feishu_app_id, app_secret=feishu_app_secret)
    # 开发旁路开着就在**启动期**喊一声。挂在这里的理由: 本函数是「装配飞书这条线」唯一的
    # 入口, 于是这条告警的可达性与旁路的可达性是同一个条件 —— 不挂飞书的进程压根没有
    # ``/feishu/auth/login``, 也就不该报旁路。
    #
    # 页面上那条常驻告警条已撤 (开发者启动时看见就够, 不必占每个用户一条通栏), 撤掉之后
    # 这里就是开发者唯一的提示。**先前根本没有启动期提示**: 唯一的痕迹是每次登录那条
    # WARNING, 所以「只删前端」会让旁路在没人登录前完全不可见。
    warn_if_dev_bypass_enabled()
    register_auth_routes(app)
    app.router.add_get("/feishu/jsapi/config", _feishu_jsapi_config)
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
    # 带鉴权的聊天流。**与骨架 ``POST /sessions/{session_id}/chat`` 不同 path**, 不是重复注册
    # —— 后者仍在, 行为一字不改。撞同 path 的后果见 ``register_auth_routes`` 里那段: aiohttp
    # 不报错, 各建一个 resource 由先注册者胜出, 表现是有效 cookie 反而拿 401。
    app.router.add_post("/feishu/sessions/{session_id}/chat", _web_chat)
    # 会话级**只读**一族: 任务进度/历史子任务/交付物下载/对话历史导出。
    #
    # 为什么要有: 网页应用的左侧任务上下文与宝箱打的就是这几条, 而骨架的
    # ``/sessions/{id}/…`` 对等物既在反代白名单之外(云上恒 404)、又一行鉴权都没有 ——
    # 后者是硬伤: 下载那条与 ``/workspace/file`` 一样能读服务器上的文件。全部落在
    # ``/feishu/sessions/`` 前缀下, 该前缀已在白名单里, **不需要动 oauth-proxy**。
    app.router.add_get("/feishu/sessions/{session_id}/todos", _web_todos)
    app.router.add_get("/feishu/sessions/{session_id}/todo-segments", _web_todo_segments)
    app.router.add_get("/feishu/sessions/{session_id}/todo-segments/{segment_id}", _web_todo_segment)
    app.router.add_get("/feishu/sessions/{session_id}/files", _web_download_file)
    app.router.add_get("/feishu/sessions/{session_id}/export", _web_export_history)
    # 会话级**写**一族(标题 / 生成标题 / 删除): 裸路由在云上被白名单挡着, 而它们都要先判
    # 归属。删除那条另有硬闸: 与机器人共用那条不许删(见 handler 的说明)。
    app.router.add_delete("/feishu/sessions/{session_id}", _web_delete_session)
    app.router.add_post("/feishu/titles", _web_set_title)
    app.router.add_post("/feishu/titles/generate", _web_generate_title)
    app.router.add_get("/feishu/titles", _web_list_titles)
    app.router.add_get("/feishu/summaries", _web_list_summaries)
    # 跨会话的只读聚合(「本月执行」那一格)。**精确路径**而不是塞进
    # ``/feishu/sessions/`` 前缀: 它不属于任何一条会话。
    app.router.add_get("/feishu/stats/monthly", _web_monthly_stats)
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
