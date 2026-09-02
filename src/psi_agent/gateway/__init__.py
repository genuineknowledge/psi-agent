"""Gateway — lifecycle manager for AI/Session instances over a REST + Web UI surface."""

from __future__ import annotations

import os
import socket
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import anyio
import tyro
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site
from psi_agent.gateway._defaults import (
    read_install_language,
    resolve_appdata_root,
    resolve_default_agent,
    resolve_default_language,
    resolve_default_workspace,
)
from psi_agent.gateway._state import GatewayState
from psi_agent.gateway.desktop._attention import AttentionHub
from psi_agent.gateway.desktop._auth_manager import AuthManager, resolve_endpoint
from psi_agent.gateway.desktop._free_model import make_key_resolver
from psi_agent.gateway.desktop._routes import register_desktop_routes
from psi_agent.gateway.desktop._spa_shell import DEFAULT_APP_NAME
from psi_agent.gateway.desktop._tray import GatewayTray
from psi_agent.gateway.desktop._ui_prefs import UIPrefs
from psi_agent.gateway.desktop._webview import GatewayWebView
from psi_agent.gateway.feishu._routes import register_feishu_routes, register_oauth_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._router_manager import RouterManager, RouterUpstreamInfo
from psi_agent.runtime._scheduler_manager import SchedulerManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager


def _random_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


GatewayName = Literal["desktop", "feishu"]
"""``--gateway`` 的取值域。加第三个 gateway 只动这里与 ``ALL_GATEWAYS``。"""

ALL_GATEWAYS: tuple[GatewayName, ...] = ("desktop", "feishu")
"""``--gateway`` 的取值域全集 (顺序即注册顺序: ToC 先贴, ``GET /`` 归它)。

**不是默认值** —— 该参数必填, 见 ``Gateway.gateway``。这里只用于 ``resolve_gateways``
的报错文案列举可选值。
"""


def resolve_gateways(selected: Sequence[str]) -> tuple[str, ...]:
    """规整 ``--gateway`` 的取值: 去重保序, 空列表报错。

    两种输入 tyro 都照收不报错, 得在这里自己拦 (实测):

    - ``--gateway`` 后不跟任何值 → ``[]``。那是起了服务但一个前端都没有的状态, 明确拒绝
      而不是静默起个空壳: 用户看不出区别, 只会在访问时拿 404 并以为服务挂了。骨架 REST
      (``/ais`` ``/sessions`` …) 想单独跑不该借这个参数, 那是另一件事。
    - ``--gateway feishu feishu`` → 重复值。去重而不是报错: 意图没有歧义 (要飞书那面),
      报错只是给脚本拼参数的人添麻烦。注册函数被调两次才是真问题 —— 同名路由叠一层。
    """
    if not selected:
        raise ValueError(f"--gateway needs at least one of {{{','.join(ALL_GATEWAYS)}}}; got an empty list")
    return tuple(dict.fromkeys(selected))


async def _redirect_to_feishu_web(request: web.Request) -> web.Response:
    """只挂 ``--gateway feishu`` 时的 ``GET /``: 302 到 ``/feishu-web/``。

    只在不挂 ToC 时注册。ToC 的 ``GET /`` 有 spa-v2 → spa 的降级链 (``desktop/_routes.py``),
    两条线都挂时根路径仍归它, 行为不变。

    刻意用重定向而不是直接返回 ToB 的 index: 静态挂载点 ``/feishu-web/`` 是 ``vite.config.ts``
    的 ``base``, 前端资源路径都以它开头; 在 ``/`` 直接吐 index 会让相对资源请求打到根下而
    404。dist 目录不存在时 ``/feishu-web/`` 本身没注册, 用户跟着跳过去拿 404 —— 与只挂
    ToC 且 dist 缺失时的表现一致 (没有产物就是没有前端), 不再额外造一个假页面。

    **指向 ``index.html`` 而不是目录** (实测): ``add_static(..., show_index=False)`` 对
    ``/feishu-web/`` 这个裸目录回 **403** 而非 index (ToC 侧靠 ``add_static`` 之前另注册
    三条 ``→ index.html`` 的 handler 绕过, 见 ``desktop/_routes.py`` 那句注释)。跳到目录
    会让 ToB 单挂时的首页变成 403; 直接跳文件即可, 无需给飞书侧补那三条 —— 补了会改动
    默认 (两面全挂) 组合的路由集合, 而那一条要求逐条不变。
    """
    return web.Response(status=302, headers={"Location": "/feishu-web/index.html"})


@dataclass
class Gateway:
    """Start the gateway REST + Web UI server."""

    listen: str = ""
    """Listen address. Empty = random high port on 127.0.0.1."""

    # **两个 Gateway 同时跑时必须给不同值** (或改 ``--default-workspace``, 见下)。冲突不来自
    # 共享前缀, 而是**同一个完整管道名**: 同 workspace 的调度 Session id 由 workspace 路径的
    # sha256 确定性派生 (``runtime/_scheduler_manager.py``), 两个进程必然算出同一个名字;
    # ``_session_manager`` 的去重只在进程内, 抓不到跨进程重名。Windows 上表现为
    # ``PermissionError(13, ...)`` / ``[WinError 5] 拒绝访问``。
    socket_path: str = "psi"
    """Prefix for AI/Session socket paths (Unix sockets on POSIX, Named Pipes on Windows).

    Give two concurrent Gateways different values, or their scheduler Sessions
    collide on one pipe name.
    """

    # 本字段的 docstring 是**用户可见的帮助文本**, 刻意写短: tyro 把它整段渲进「缺必填参数」
    # 的报错框里 (实测), 写长了会把「你少给了一个参数」这一句话淹在几十行说明里。设计理由与
    # 实测记录都放这条注释里, 不进 docstring。
    #
    # **为什么必填、没有默认值**: 挂哪些面是**部署方的决定**, 内核不替它猜。原先默认全集
    # ``("desktop", "feishu")`` 看着安全实则最危险 —— 少挂一面不报错, 只是某个前端 404,
    # 出问题时排查方向完全跑偏。必填把这个静默失败提前成启动期的显式失败。
    #
    # **为什么不按环境给默认值** (比如云上默认 feishu): 内核里没有「产品线」这个概念 (定过),
    # 要让内核默认 feishu, 内核就得先知道自己跑在云上 —— 那等于把产品线概念从参数名里赶
    # 出去、又从环境判断偷偷放回来。哪一面该挂是部署脚本的事, 见 ``AGENTS.md``。
    #
    # **为什么用 ``tyro.MISSING`` 而非省略默认值**: 本字段前面的字段都带默认值, 真省掉会撞
    # dataclass 的「非默认字段不能跟在默认字段后」而 ``TypeError``。``tyro.MISSING`` 在
    # tyro 眼里是必填, 对 dataclass 而言又是个普通默认值, 于是不必重排字段顺序 (字段顺序
    # 就是 ``--help`` 里的显示顺序, 为一个约束去重排会牵动无关的可读性)。
    #
    # 实测 (tyro 1.0.15 / Python 3.14.7):
    #
    # - ``--help``: 该项列在 ``options`` 段, 行尾标 ``(required)``; 值域直接印在参数名后
    #   (``--gateway [{desktop,feishu} [{desktop,feishu} ...]]``)。
    # - 不传: 退出码 **2**, 打一个 ``Required options`` 框, ``Missing from <prog> gateway:``
    #   后跟该参数与值域 —— 缺什么、可选值是什么都在里面。
    # - ``dataclasses.fields(Gateway)`` 上此字段的 ``default`` 是 ``tyro.MISSING``
    #   (``tyro._singleton.PropagatingMissingType``), **不是** ``dataclasses.MISSING``;
    #   ``default_factory`` 才是 ``dataclasses.MISSING``。判「有没有默认值」得认前者。
    # - 必填**不覆盖**空列表那条: ``--gateway`` 后不跟任何值仍给 ``[]`` 且退出码 0 —— tyro
    #   认为参数「给过了」。那一步的拦截仍归 ``resolve_gateways``, 别以为必填替掉了它。
    #
    # 各值挂哪些路由: ``desktop`` = ToC 那面 (``/spa/`` ``/spa-v2/`` ``/ui/*``
    # ``/workspace/*`` ``/auth/*``); ``feishu`` = ToB 那面 (``/feishu/*``
    # ``/feishu-web/``, 单挂时 ``GET /`` 302 到 ``/feishu-web/index.html``)。两个都写则
    # 两面全挂; 只写一个, 另一面的路由不注册 (404)。逗号形式不支持。与 ``--default-agent``
    # 是两个独立维度, 可自由组合。
    gateway: list[GatewayName] = tyro.MISSING
    """Which gateway HTTP surfaces to mount (required, space-separated, combinable)."""

    icon: str | None = None
    """Path to icon image file (png/jpg/ico). Used as favicon, tray icon (--tray), and webview icon (--webview)."""

    app_name: str = DEFAULT_APP_NAME
    """Browser tab / webview / tray label. Injected into SPA index.html at serve time."""

    browser: bool = False
    """Open a browser tab on startup."""

    webview: bool = False
    """Use a native webview window instead of the system browser."""

    tray: bool = False
    """Show a system tray icon (requires --icon)."""

    # 飞书 channel 经 ``POST /feishu/route`` 按需为每个飞书用户/群 spawn 独立 Session 时用它
    # 作缺省 AI (请求体也可逐次覆盖 ``ai_id``)。空 = 未配, 此时若请求也不带 ``ai_id`` 则
    # ``/feishu/route`` 返回 400。
    feishu_ai_id: str = ""
    """Default AI instance id for Feishu Sessions. Empty = unset (requests must carry ai_id)."""

    # 私聊每个 open_id 得到 ``<root>/<open_id>`` 子目录, 群聊每个 chat_id 得到
    # ``<root>/chat-<chat_id>``, 文件/历史互相隔离。
    feishu_workspace_root: str = ""
    """Parent dir for per-conversation Feishu workspaces. Empty = Gateway process cwd."""

    # 网页应用免登要用这对凭证: 前端经 ``GET /feishu/app-id`` 取 app_id, 传给
    # ``tt.requestAccess`` 换 code; 后端再拿 app_id + secret 把 code 换成
    # ``user_access_token``。**secret 永不下发前端** —— 那个端点只回 app_id。
    #
    # 与 channel 侧读的是同一对凭证 (同一个自建应用), 但两个进程各自读环境变量, 不互相
    # 传递。未配时 ``/feishu/auth/login`` 回 400 而非 500, 前端显示「未配置免登」。
    feishu_app_id: str = ""
    """Feishu app ID for web-app SSO (CLI > env ``PSI_FEISHU_APP_ID``). Empty = SSO off (400)."""

    feishu_app_secret: str = ""
    """Feishu app secret (CLI > env ``PSI_FEISHU_APP_SECRET``). Server-side only, never sent to the client."""

    # 非空值两形解析 (见 ``_defaults.resolve_default_agent``): 先试值本身是目录, 再试
    # ``agents/<值>``, 都不是则**报错退出**。原先第一档不查存在性, ``--default-agent desktop``
    # 静默指向 ``{cwd}/desktop`` —— 启动期不碰这个路径, 日志干净端口正常, 错要等建 Session
    # 才暴露成「这个 Session 没有 tools/skills」。
    #
    # 空值是有意义的第三态, 不报错: 软默认 ``agents/feishu`` → cwd 像装机布局 (``tools/`` +
    # ``skills/``) → Session 单根兼容 (``agent=""`` 即等于 workspace)。
    default_agent: str = ""
    """Default agent package for new Sessions / GET /defaults.

    A short name selects ``agents/<name>``; a path is used as given. Unknown
    values exit with the available names. Empty = soft default.
    """

    # 空值 → 软默认 ``{Desktop}/haitun交付``, 且**只公告路径不建目录**: 目录在第一次建
    # Session / 开对话时才建, 开一下 Haitun 不该在桌面留个空文件夹。
    #
    # 这里不是 AppData: todos / history / Gateway state 都在 ``--appdata`` 下。
    default_workspace: str = ""
    """Default user workspace for new Sessions / GET /defaults.

    Empty = soft default under the OS Desktop. Not the AppData root (see --appdata).
    """

    language: str = ""
    """UI language: ``zh-CN`` (default), ``zh-TW`` or ``en-US``.

    Empty → ``HAITUN_LANG`` env → installer-written ``haitun-language.txt`` under
    the agent package → ``zh-CN``.  The SPA can still switch languages in-app;
    that choice is persisted in AppData and wins over this flag on later boots.
    """

    appdata: str = ""
    """AppData memory-area root (``GET /defaults.appdata``, env ``PSI_APPDATA``).

    Empty → ``PSI_APPDATA`` → ``platformdirs.user_data_dir(Haitun)``.
    Step 4B: todos write under ``{appdata}/todos/`` (legacy workspace path dual-read).
    Step 4C: history writes under ``{appdata}/histories/`` (legacy dual-read).
    Step 4D: Gateway ``state/`` under ``{appdata}/state/`` (legacy cwd dual-read).
    """

    # 每个 workspace 会得到一个专用调度 Session (对 SPA / state 隐藏), 以
    # ``active_schedules=("*",)`` 激活该 workspace 下的全部定时任务 —— 定时任务从 workspace
    # 加载, 但**触发权是 (session x schedule) 逐条的**, 一条必须恰好被一个 Session 激活,
    # 否则飞书多用户下一条提醒会被在线会话数乘一遍。
    scheduler_ai_id: str = ""
    """AI instance id for the per-workspace scheduler Session.

    Empty = fall back to --feishu-ai-id; both empty = no scheduler Session (warns).
    """

    # 留空取的内置默认值是**账号服务的正式地址**。
    #
    # **空 ≠ 关闭**: 装了包的用户起 Gateway 就该能登录, 要求他先知道并手填一个域名, 等于把
    # 部署细节转嫁给使用者。要**关掉**认证 (纯本地单用户, 不注册 ``/auth/*``、不读写本机凭证)
    # 请显式设 ``PSI_AUTH_ENDPOINT=""``。
    #
    # 启用时客户端只做转发与本机凭证管理: 不持任何供应商密钥 (安装包里放阿里云 AK/SK 或
    # Resend key 等于公开发布), 授权判定全在云端 (用户本人即机器管理员, 客户端侧校验可被
    # 绕过)。见 ``_auth_manager.resolve_endpoint``。
    auth_endpoint: str = ""
    """Cloud auth service address. Empty = built-in default (not disabled).

    Set PSI_AUTH_ENDPOINT="" to turn auth off entirely.
    """

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        if self.browser and self.webview:
            raise ValueError("--browser and --webview are mutually exclusive")

        # 与上面的互斥校验同处: 都在建 socket / 恢复 state 之前失败, 不留半启动的进程。
        gateways = resolve_gateways(self.gateway)

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        # Path defaults: agent/workspace (Step 2) + AppData root announce (Step A).
        agent_default = await resolve_default_agent(self.default_agent)
        workspace_default = await resolve_default_workspace(self.default_workspace)
        appdata_root = await resolve_appdata_root(self.appdata)
        prefs = await UIPrefs.from_appdata(appdata_root)
        install_language = await read_install_language(agent_default)
        language = await resolve_default_language(
            self.language,
            install_language=install_language,
            user_language=await prefs.language(),
            install_language_seen=await prefs.install_language_seen(),
        )
        if install_language:
            await prefs.set_install_language_seen(install_language)
        # So in-process Session tools (todo, …) see the same root as GET /defaults.
        os.environ["PSI_APPDATA"] = appdata_root
        # Workspace system-prompt builders and channels read this for default language.
        os.environ["HAITUN_LANG"] = language
        logger.info(f"Default agent: {agent_default or '(same as workspace)'}")
        logger.info(f"Default workspace: {workspace_default}")
        logger.info(f"AppData root: {appdata_root}")
        logger.info(f"UI language: {language}")

        state = await GatewayState.from_appdata(appdata_root)
        snapshot = await state.load()

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            rm = RouterManager(_aim=aim, _prefix=self.socket_path, _tg=tg)
            sm = SessionManager(
                _aim=aim,
                _rm=rm,
                _prefix=self.socket_path,
                _tg=tg,
                _default_agent=agent_default,
                _default_workspace=workspace_default,
                _appdata=appdata_root,
            )
            tm = TitleManager()
            sum_m = SummaryManager()

            # 认证是**旁挂**的: 不注入 Session 的构造参数, 不写 ContextVar, 不参与
            # _do_persist 的 manager 快照 (凭证不进 state/latest.json —— 那里的
            # api_key 是明文, 登录凭证不再踩这个坑)。地址显式为空则整套不加载。
            #
            # ** 必须建在恢复 AI 之前 **: 免费模型的 socket 在构造时就要拿到 token,
            # 建晚了恢复出来的 socket 会带着哨兵值起来, 第一次对话必然 401。
            authm: AuthManager | None = None
            if resolve_endpoint(self.auth_endpoint):
                authm = await AuthManager.create(self.auth_endpoint, appdata_root=appdata_root, tg=tg)
                # 免费模型的哨兵值换成登录 token。传的是取值函数而不是 token ——
                # socket 重建时要拿到当时的新值, 不是接线那一刻的旧值。
                aim._resolve_key = make_key_resolver(authm.bearer_token, authm.endpoint)
                # 趁用户还没点「获取验证码」, 先把连接建好, 省下 TCP+TLS 两个 RTT。
                await authm.nudge_warm()
            else:
                logger.info("Auth disabled (PSI_AUTH_ENDPOINT set to empty)")

            for cfg in snapshot.get("ais", []):
                try:
                    await aim.create(
                        provider=cfg.get("provider", ""),
                        model=cfg.get("model", ""),
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        id=cfg.get("id", ""),
                        max_context_tokens=int(cfg.get("max_context_tokens", -1)),
                    )
                    logger.info(f"Restored AI {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore AI {cfg.get('id', '?')!r}: {e!r}")

            for cfg in snapshot.get("routers", []):
                try:
                    await rm.create(
                        name=cfg.get("name", ""),
                        mode=cfg.get("mode", ""),
                        router_ai_id=cfg.get("router_ai_id"),
                        upstreams=[
                            RouterUpstreamInfo(
                                backend_type=item.get("backend_type", ""),
                                backend_id=item.get("backend_id", ""),
                                description=item.get("description", ""),
                            )
                            for item in cfg.get("upstreams", [])
                        ],
                        router_timeout=cfg.get("router_timeout"),
                        target_timeout=cfg.get("target_timeout"),
                        max_context_chars=cfg.get("max_context_chars", 12_000),
                        id=cfg.get("id", ""),
                    )
                    logger.info(f"Restored Router {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore Router {cfg.get('id', '?')!r}: {e!r}")

            for cfg in snapshot.get("sessions", []):
                try:
                    await sm.create(
                        backend_type=cfg.get("backend_type", "ai"),
                        backend_id=cfg.get("backend_id", cfg.get("ai_id", "")),
                        workspace=cfg.get("workspace", ""),
                        agent=cfg.get("agent", "") or agent_default,
                        id=cfg.get("id", ""),
                    )
                    logger.info(f"Restored Session {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore Session {cfg.get('id', '?')!r}: {e!r}")

            for t in snapshot.get("titles", []):
                await tm.set(t["id"], t["title"])

            for row in snapshot.get("summaries", []):
                await sum_m.set(row["id"], row["summary"])

            attention = AttentionHub()
            schedm = SchedulerManager(
                _sm=sm,
                _ai_id=self.scheduler_ai_id or self.feishu_ai_id,
                # 公司级种子任务: 部署时经 PSI_SEED_SCHEDULES_WORKSPACE 指定落点
                # workspace, 种子来源是 agent 包自带的 schedules/。空 = 关闭 seed。
                seed_workspace=os.environ.get("PSI_SEED_SCHEDULES_WORKSPACE", ""),
                seed_agent=agent_default,
            )
            # 首个定时任务由 watch_loop 自动拉起: ensure 只会在「schedules 已存在」时
            # spawn, 而 schedule_manage 写第一个 TASK.md 不会触发 ensure —— 没有这个
            # 常驻协程, 用户新建的定时任务要等下一次 ensure 碰巧发生才生效 (到点不触发)。
            tg.start_soon(schedm.watch_loop)
            # 骨架 + 按 --gateway 贴各 gateway 的 HTTP 面。**贴哪些完全由调用方给定** ——
            # 该参数必填, 没有「不传时挂什么」这回事: 少挂一面的表现是某个前端 404 而非
            # 报错, 所以宁可在启动期就要求说清楚。生产上飞书容器起的也是 `psi-agent
            # gateway` (同容器里另起一个 `psi-agent channel feishu` 连过来), 它得显式写
            # `--gateway feishu`; 装机版显式写 `--gateway desktop`。开发时只写一个值单挂
            # 一面, 省掉另一面的前端与 manager。
            want_desktop = "desktop" in gateways
            want_feishu = "feishu" in gateways
            logger.info(f"Gateways: {' '.join(gateways)} (desktop={want_desktop}, feishu={want_feishu})")
            app = await create_core_app(
                aim,
                sm,
                tm,
                rm=rm,
                default_agent=agent_default,
                default_workspace=workspace_default,
                language=language,
                appdata=appdata_root,
                scheduler_ai_id=self.scheduler_ai_id,
                schedm=schedm,
                sum_m=sum_m,
            )
            if want_desktop:
                await register_desktop_routes(
                    app,
                    favicon_path=self.icon,
                    app_name=self.app_name,
                    attention=attention,
                    authm=authm,
                )
            if want_feishu:
                register_feishu_routes(
                    app,
                    feishu_ai_id=self.feishu_ai_id,
                    feishu_workspace_root=self.feishu_workspace_root,
                    feishu_app_id=self.feishu_app_id or os.environ.get("PSI_FEISHU_APP_ID", ""),
                    feishu_app_secret=self.feishu_app_secret or os.environ.get("PSI_FEISHU_APP_SECRET", ""),
                )
                # ``GET /`` 的兜底链住在 ToC 那边 (spa-v2 → spa)。只挂飞书时那条链没注册,
                # 根路径得自己交代去处, 否则用户访问裸地址拿到 404 还以为服务没起来。
                if not want_desktop:
                    app.router.add_get("/", _redirect_to_feishu_web)
            else:
                # 只挂 ToC: ``/oauth/*`` 随飞书装配一起没了, 这里补上 —— 回调地址登记在
                # 第三方应用后台, 少这两条就是用户点完授权落到 404。
                register_oauth_routes(app)

            # Restored sessions need a scheduler Session for their workspace too
            # (on demand: skipped when there are no schedules).
            for info in await sm.list_all():
                await schedm.ensure(info.workspace, ai_id=info.backend_id, agent=info.agent)

            async def _do_persist() -> None:
                await state.save(
                    ais=[
                        {
                            "id": info.id,
                            "provider": info.provider,
                            "model": info.model,
                            "api_key": info.api_key,
                            "base_url": info.base_url,
                            "max_context_tokens": info.max_context_tokens,
                        }
                        for info in await aim.list_all()
                    ],
                    sessions=[
                        {
                            "id": info.id,
                            "backend_type": info.backend_type,
                            "backend_id": info.backend_id,
                            "workspace": info.workspace,
                            "agent": info.agent,
                        }
                        for info in await sm.list_all()
                    ],
                    titles=[{"id": sid, "title": title} for sid, title in tm.get_all().items()],
                    summaries=[{"id": sid, "summary": text} for sid, text in sum_m.get_all().items()],
                    routers=[
                        {
                            "id": info.id,
                            "name": info.name,
                            "mode": info.mode,
                            "router_ai_id": info.router_ai_id,
                            "upstreams": [
                                {
                                    "backend_type": item.backend_type,
                                    "backend_id": item.backend_id,
                                    "description": item.description,
                                }
                                for item in info.upstreams
                            ],
                            "router_timeout": info.router_timeout,
                            "target_timeout": info.target_timeout,
                            "max_context_chars": info.max_context_chars,
                        }
                        for info in await rm.list_all()
                    ],
                )

            aim._persist = _do_persist
            rm._persist = _do_persist
            sm._persist = _do_persist
            tm._persist = _do_persist
            sum_m._persist = _do_persist

            await _do_persist()

            runner = web.AppRunner(app)
            try:
                try:
                    await runner.setup()
                    site = create_site(runner, addr)
                    await site.start()
                except Exception as e:
                    logger.error(f"Failed to start Gateway on {addr}: {e!r}")
                    raise

                logger.info(f"Gateway listening on {addr}")

                wv = None
                if self.webview:
                    if self.icon is None:
                        raise ValueError("--webview requires --icon to be set")
                    wv = GatewayWebView(addr, has_tray=self.tray, icon=self.icon, app_name=self.app_name)
                    try:
                        wv.start()
                    except Exception as e:
                        logger.warning(f"Failed to start webview window: {e!r}")

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    if self.icon is None:
                        raise ValueError("--tray requires --icon to be set")
                    on_open = wv.show if wv is not None and wv.is_running() else None
                    tray = GatewayTray(addr, self.icon, app_name=self.app_name, on_open=on_open)
                    try:
                        tray.start()
                    except Exception as e:
                        logger.warning(f"Failed to start system tray: {e!r}")

                if wv is not None and wv.is_running():
                    attention.bind(webview=wv)
                if tray is not None and tray.is_running():
                    attention.bind(tray=tray)

                try:
                    if tray is not None and tray.is_running():
                        await anyio.to_thread.run_sync(tray.wait_stop, abandon_on_cancel=True)  # ty: ignore
                    elif wv is not None and wv.is_running():
                        await anyio.to_thread.run_sync(wv.wait_closed, abandon_on_cancel=True)  # ty: ignore
                    else:
                        await anyio.sleep_forever()
                finally:
                    if tray is not None:
                        tray.stop()
                    if wv is not None:
                        wv.stop()
            finally:
                logger.info("Shutting down Gateway")
                with anyio.CancelScope(shield=True):
                    await runner.cleanup()
                    # AuthManager 持有 aiohttp 会话, 必须显式关闭, 否则退出时报
                    # "Unclosed client session"。放 shield 内: 被取消时也要清。
                    if authm is not None:
                        await authm.aclose()
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
