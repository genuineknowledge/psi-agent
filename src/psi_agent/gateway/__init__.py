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

    tray: bool = True
    """Show a system tray icon (left-click opens the Web Console). Use --no-tray to skip it."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        tg = anyio.create_task_group()
        await tg.__aenter__()

        aim = AIManager(_prefix=self.socket_path, _tg=tg)
        sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

        app = create_app(aim, sm)
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = create_site(runner, addr)
            await site.start()
        except Exception as e:
            logger.error(f"Failed to start Gateway on {addr}: {e}")
            with anyio.CancelScope(shield=True):
                await runner.cleanup()
            with anyio.CancelScope(shield=True):
                await tg.__aexit__(None, None, None)
            raise

        logger.info(f"Gateway listening on {addr}")

        if self.browser:
            await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

        tray: GatewayTray | None = None
        if self.tray:
            tray = GatewayTray(addr)
            try:
                tray.start()
            except Exception as e:
                logger.warning(f"Failed to start system tray: {e}")
        else:
            logger.info("System tray disabled (--no-tray); running until cancelled")

        try:
            if tray is not None:
                await anyio.to_thread.run_sync(tray._stop_event.wait)  # ty: ignore
            else:
                await anyio.sleep_forever()
        finally:
            if tray is not None:
                tray.stop()
            logger.info("Shutting down Gateway")
            with anyio.CancelScope(shield=True):
                await runner.cleanup()
            with anyio.CancelScope(shield=True):
                await tg.__aexit__(None, None, None)
            logger.info("Gateway shutdown complete")
