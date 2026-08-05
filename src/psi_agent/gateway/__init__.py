"""Gateway — lifecycle manager for AI/Session instances over a REST + Web UI surface."""

from __future__ import annotations

import os
import socket
import webbrowser
from dataclasses import dataclass

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site
from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._attention import AttentionHub
from psi_agent.gateway._defaults import resolve_appdata_root, resolve_default_agent, resolve_default_workspace
from psi_agent.gateway._router_manager import RouterManager, RouterUpstreamInfo
from psi_agent.gateway._scheduler_manager import SchedulerManager
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME
from psi_agent.gateway._state import GatewayState
from psi_agent.gateway._summary_manager import SummaryManager
from psi_agent.gateway._title_manager import TitleManager
from psi_agent.gateway._tray import GatewayTray
from psi_agent.gateway._webview import GatewayWebView
from psi_agent.gateway.server import create_app


def _random_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@dataclass
class Gateway:
    """Start the gateway REST + Web UI server."""

    listen: str = ""
    """Listen address. Empty = random high port on 127.0.0.1."""

    socket_path: str = "psi"
    """Prefix for AI/Session socket paths (Unix sockets on POSIX, Named Pipes on Windows)."""

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

    feishu_ai_id: str = ""
    """飞书 Session 默认挂载的 AI 实例 id。飞书 channel 经 ``POST /feishu/route`` 按需为每个
    飞书用户/群 spawn 独立 Session 时用它作缺省 AI (请求体也可逐次覆盖 ``ai_id``)。空 = 未配,
    此时若请求也不带 ``ai_id`` 则 ``/feishu/route`` 返回 400。"""

    feishu_workspace_root: str = ""
    """飞书各会话独立 workspace 的父目录。私聊每个 open_id 得到 ``<root>/<open_id>`` 子目录,
    群聊每个 chat_id 得到 ``<root>/chat-<chat_id>``, 文件/历史互相隔离。空 = 以 Gateway 进程
    cwd 为父目录。"""

    default_agent: str = ""
    """CLI: default agent package for new Sessions / GET /defaults.

    Empty → soft-default ``examples/haitun-workspace`` under cwd when present;
    else cwd when it looks like an install layout (``tools/`` + ``skills/``);
    else Session keeps single-root compat (``agent=\"\"`` → same as workspace).
    """

    default_workspace: str = ""
    """Step 2 CLI: default user workspace for new Sessions / GET /defaults.

    Empty → soft-default ``{Desktop}/haitun交付`` (path announced only; directory
    created on first Session / conversation, not on Gateway boot).
    Not AppData; todos/history/Gateway state live under ``--appdata``.
    """

    appdata: str = ""
    """AppData memory-area root (``GET /defaults.appdata``, env ``PSI_APPDATA``).

    Empty → ``PSI_APPDATA`` → ``platformdirs.user_data_dir(Haitun)``.
    Step 4B: todos write under ``{appdata}/todos/`` (legacy workspace path dual-read).
    Step 4C: history writes under ``{appdata}/histories/`` (legacy dual-read).
    Step 4D: Gateway ``state/`` under ``{appdata}/state/`` (legacy cwd dual-read).
    """

    scheduler_ai_id: str = ""
    """调度 Session 挂载的 AI 实例 id。每个 workspace 会得到一个专用调度 Session
    (对 SPA / state 隐藏), 以 ``active_schedules=("*",)`` 激活该 workspace 下的全部
    定时任务 —— 定时任务从 workspace 加载, 但**触发权是 (session x schedule) 逐条的**,
    一条必须恰好被一个 Session 激活, 否则飞书多用户下一条提醒会被在线会话数乘一遍。

    空 = 回落 ``--feishu-ai-id``; 两者都空则不启动调度 Session (记 warning)。
    """

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        if self.browser and self.webview:
            raise ValueError("--browser and --webview are mutually exclusive")

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        # Path defaults: agent/workspace (Step 2) + AppData root announce (Step A).
        agent_default = await resolve_default_agent(self.default_agent)
        workspace_default = await resolve_default_workspace(self.default_workspace)
        appdata_root = await resolve_appdata_root(self.appdata)
        # So in-process Session tools (todo, …) see the same root as GET /defaults.
        os.environ["PSI_APPDATA"] = appdata_root
        logger.info(f"Default agent: {agent_default or '(same as workspace)'}")
        logger.info(f"Default workspace: {workspace_default}")
        logger.info(f"AppData root: {appdata_root}")

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
            schedm = SchedulerManager(_sm=sm, _ai_id=self.scheduler_ai_id or self.feishu_ai_id)
            app = await create_app(
                aim,
                sm,
                tm,
                rm=rm,
                favicon_path=self.icon,
                app_name=self.app_name,
                attention=attention,
                feishu_ai_id=self.feishu_ai_id,
                feishu_workspace_root=self.feishu_workspace_root,
                default_agent=agent_default,
                default_workspace=workspace_default,
                appdata=appdata_root,
                scheduler_ai_id=self.scheduler_ai_id,
                schedm=schedm,
                sum_m=sum_m,
            )

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
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
