"""Semantic model routing service."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site

from .models import parse_upstreams
from .server import ROUTER_SETTINGS_KEY, RouterSettings, handle_router_chat_completions


async def serve_router(*, socket_path: str, settings: RouterSettings) -> None:
    logger.info(f"Starting semantic AI router on {socket_path} with {len(settings.targets)} upstreams")
    app = web.Application()
    app[ROUTER_SETTINGS_KEY] = settings
    app.router.add_post("/chat/completions", handle_router_chat_completions)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, socket_path)
        await site.start()
    except Exception as exc:
        logger.error(f"Failed to start semantic AI router on {socket_path}: {exc}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise
    logger.info(f"Semantic AI router listening on {socket_path}")
    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down semantic AI router on {socket_path}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        logger.info(f"Semantic AI router shutdown complete on {socket_path}")


@dataclass
class Router:
    """Route Chat Completions requests using candidate descriptions."""

    session_socket: str
    """Path or URL on which the router listens for Session requests."""

    router_socket: str = ""
    """Candidate model socket used to make routing decisions."""

    upstream: list[str] = field(default_factory=list)
    """Candidate JSON objects with socket and description."""

    default_socket: str = ""
    """Fallback service address used when semantic selection fails."""

    router_timeout: float | None = None
    """Optional finite positive selection timeout in seconds."""

    router_context_chars: int = 12_000
    """Maximum serialized conversation characters sent for selection."""

    log_router_details: bool = False
    """Log only the routing reason in addition to the final result."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        if not self.router_socket.strip():
            raise ValueError("--router-socket must be provided")
        if not self.default_socket.strip():
            raise ValueError("--default-socket must be provided")
        if self.router_context_chars <= 0:
            raise ValueError("--router-context-chars must be positive")
        if self.router_timeout is not None and (not math.isfinite(self.router_timeout) or self.router_timeout <= 0):
            raise ValueError("--router-timeout must be a finite positive number")
        targets = parse_upstreams(self.upstream)
        router_socket = self.router_socket.strip()
        settings = RouterSettings(
            targets=targets,
            router_socket=router_socket,
            default_socket=self.default_socket.strip(),
            router_timeout=self.router_timeout,
            context_chars=self.router_context_chars,
            log_details=self.log_router_details,
        )
        logger.debug(
            f"Router resolved params: router_socket={settings.router_socket!r}, "
            f"upstreams={len(settings.targets)}, default_socket={settings.default_socket!r}"
        )
        await serve_router(socket_path=self.session_socket, settings=settings)
