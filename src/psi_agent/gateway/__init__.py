"""Gateway — lifecycle manager for AI/Session instances over a REST + Web UI surface."""

from __future__ import annotations

import socket
import webbrowser
from dataclasses import dataclass
from datetime import datetime

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site
from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._state import GatewayState
from psi_agent.gateway._title_manager import TitleManager
from psi_agent.gateway._tray import GatewayTray
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

    verbose: bool = False
    """Enable DEBUG-level logging."""

    browser: bool = True
    """Open a browser tab on startup."""

    tray: str | None = None
    """Path to tray icon image file. If set, a system tray icon is shown."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        state = GatewayState(
            _path=anyio.Path("state/latest.json"),
            _startup_ts=datetime.now().strftime("%Y%m%d-%H%M%S"),
        )
        snapshot = await state.load()

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

            for ai_id, cfg in snapshot.get("ais", {}).items():
                try:
                    await aim.create(
                        provider=cfg.get("provider", ""),
                        model=cfg.get("model", ""),
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        id=ai_id,
                    )
                    logger.info(f"Restored AI {ai_id!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore AI {ai_id!r}: {e!r}")

            for sess_id, cfg in snapshot.get("sessions", {}).items():
                try:
                    await sm.create(
                        ai_id=cfg.get("ai_id", ""),
                        workspace=cfg.get("workspace", ""),
                        id=sess_id,
                    )
                    logger.info(f"Restored Session {sess_id!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore Session {sess_id!r}: {e!r}")

            app = await create_app(aim, sm, favicon_path=self.tray)
            tm: TitleManager = app["tm"]

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
                        {"id": info.id, "ai_id": info.ai_id, "workspace": info.workspace}
                        for info in await sm.list_all()
                    ],
                    titles=tm.get_all(),
                )

            aim._persist = _do_persist
            sm._persist = _do_persist
            tm._persist = _do_persist

            for sid, title in snapshot.get("titles", {}).items():
                await tm.set(sid, title)

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

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    tray = GatewayTray(addr, self.tray)
                    try:
                        tray.start()
                    except Exception as e:
                        logger.warning(f"Failed to start system tray: {e!r}")

                try:
                    if tray is not None and tray.is_running():
                        await anyio.to_thread.run_sync(tray.wait_stop, abandon_on_cancel=True)  # ty: ignore
                    else:
                        await anyio.sleep_forever()
                finally:
                    if tray is not None:
                        tray.stop()
            finally:
                logger.info("Shutting down Gateway")
                with anyio.CancelScope(shield=True):
                    await runner.cleanup()
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
