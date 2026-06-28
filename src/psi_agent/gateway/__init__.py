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
from psi_agent.gateway._manager import AIManager, SessionManager
from psi_agent.gateway.server import create_app


def _random_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@dataclass
class Gateway:
    """Start the gateway REST + Web UI server."""

    listen: str = ""
    """Listen address. Empty = random high port on 127.0.0.1."""

    socket_path: str = "psi"
    """Prefix for AI/Session Unix socket paths."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    no_open: bool = False
    """Suppress auto-opening the browser on startup."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        tg = anyio.create_task_group()
        await tg.__aenter__()

        aim = AIManager(_prefix=self.socket_path, _tg=tg)
        sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

        app = create_app(aim, sm)
        runner = web.AppRunner(app)
        await runner.setup()
        site = create_site(runner, addr)
        await site.start()
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        if not self.no_open:
            webbrowser.open(addr)

        try:
            await anyio.sleep_forever()
        finally:
            logger.info("Shutting down Gateway")
            with anyio.CancelScope(shield=True):
                await runner.cleanup()
            await tg.__aexit__(None, None, None)
