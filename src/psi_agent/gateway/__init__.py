"""Gateway — lifecycle manager for AI/Session instances over a REST + Web UI surface."""

from __future__ import annotations

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
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME
from psi_agent.gateway._state import GatewayState
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
    """Prefix for AI/Session Unix socket paths."""

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
    """飞书用户 Session 默认挂载的 AI 实例 id。飞书 channel 经 ``POST /feishu/route`` 按需为
    每个飞书用户 spawn 独立 Session 时用它作缺省 AI (请求体也可逐次覆盖 ``ai_id``)。空 = 未配,
    此时若请求也不带 ``ai_id`` 则 ``/feishu/route`` 返回 400。"""

    feishu_workspace_root: str = ""
    """飞书各用户独立 workspace 的父目录。每个 open_id 得到 ``<root>/<open_id>`` 子目录, 文件/历史
    互相隔离。空 = 以 Gateway 进程 cwd 为父目录。"""

    app_data_root: str = ""
    """Override AppData root (platformdirs by default). Env ``PSI_APP_DATA_ROOT`` also works."""

    default_agent: str = ""
    """Default agent package path. Empty → ``examples/haitun`` (via ``default_agent_path``)."""

    default_workspace: str = ""
    """Default user workspace. Empty → process cwd. SPA may override per session via POST /sessions."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        if self.browser and self.webview:
            raise ValueError("--browser and --webview are mutually exclusive")

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        from psi_agent._app_paths import (
            app_data_root as resolve_app_data,
            default_agent_path,
            default_workspace_path,
            history_dir,
            state_dir,
        )

        app_override = self.app_data_root.strip() or None
        agent_default = self.default_agent.strip() or str(default_agent_path())
        workspace_default = self.default_workspace.strip() or str(default_workspace_path())
        logger.info(f"AppData root: {resolve_app_data(override=app_override)}")
        logger.info(f"State dir: {state_dir(override=app_override)}")
        logger.info(f"History dir: {history_dir(override=app_override)}")
        logger.info(f"Default agent: {agent_default}")
        logger.info(f"Default workspace: {workspace_default}")

        state = GatewayState(app_data_root=app_override)
        snapshot = await state.load()

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            sm = SessionManager(
                _aim=aim,
                _prefix=self.socket_path,
                _tg=tg,
                _default_agent=agent_default,
                _default_workspace=workspace_default,
                _app_data_root=app_override or "",
            )
            tm = TitleManager()

            for cfg in snapshot.get("ais", []):
                try:
                    await aim.create(
                        provider=cfg.get("provider", ""),
                        model=cfg.get("model", ""),
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        id=cfg.get("id", ""),
                    )
                    logger.info(f"Restored AI {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore AI {cfg.get('id', '?')!r}: {e!r}")

            for cfg in snapshot.get("sessions", []):
                try:
                    await sm.create(
                        ai_id=cfg.get("ai_id", ""),
                        workspace=cfg.get("workspace", ""),
                        agent=cfg.get("agent", "") or agent_default,
                        id=cfg.get("id", ""),
                    )
                    logger.info(f"Restored Session {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore Session {cfg.get('id', '?')!r}: {e!r}")

            for t in snapshot.get("titles", []):
                await tm.set(t["id"], t["title"])

            attention = AttentionHub()
            app = await create_app(
                aim,
                sm,
                tm,
                favicon_path=self.icon,
                app_name=self.app_name,
                attention=attention,
                feishu_ai_id=self.feishu_ai_id,
                feishu_workspace_root=self.feishu_workspace_root,
                app_data_root=app_override or "",
                default_agent=agent_default,
                default_workspace=workspace_default,
            )

            async def _do_persist() -> None:
                await state.save(
                    ais=[
                        {
                            "id": info.id,
                            "provider": info.provider,
                            "model": info.model,
                            "api_key": info.api_key,
                            "base_url": info.base_url,
                        }
                        for info in await aim.list_all()
                    ],
                    sessions=[
                        {
                            "id": info.id,
                            "ai_id": info.ai_id,
                            "workspace": info.workspace,
                            "agent": info.agent,
                        }
                        for info in await sm.list_all()
                    ],
                    titles=[{"id": sid, "title": title} for sid, title in tm.get_all().items()],
                )

            aim._persist = _do_persist
            sm._persist = _do_persist
            tm._persist = _do_persist

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
