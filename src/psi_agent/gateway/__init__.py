"""Gateway — lifecycle manager for AI/Session instances over a REST + Web UI surface."""

from __future__ import annotations

import socket
import webbrowser
from dataclasses import dataclass

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import serve_app
from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._session_manager import SessionManager
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

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

            app = await create_app(aim, sm, favicon_path=self.tray)

            async def _run_extra() -> None:
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

            tg.start_soon(_run_extra)
            try:
                await serve_app(app, addr, name="Gateway")
            finally:
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
