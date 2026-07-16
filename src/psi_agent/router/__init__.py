"""Semantic model routing service."""

from __future__ import annotations

import math
import os
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

    router_model: str = ""
    """Model used only to select a candidate upstream."""

    router_base_url: str = ""
    """OpenAI-compatible service address for the routing model."""

    router_api_key: str = ""
    """API key for the routing model; an empty key is allowed."""

    upstream: list[str] = field(default_factory=list)
    """Candidate JSON objects with model_name, addr, and description."""

    default_addr: str = ""
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
        router_model = self.router_model or os.environ.get("PSI_ROUTER_MODEL", "")
        router_base_url = self.router_base_url or os.environ.get("PSI_ROUTER_BASE_URL", "")
        router_api_key = self.router_api_key or os.environ.get("PSI_ROUTER_API_KEY", "")
        if not router_model.strip():
            raise ValueError("--router-model or PSI_ROUTER_MODEL must be provided")
        if not router_base_url.strip():
            raise ValueError("--router-base-url or PSI_ROUTER_BASE_URL must be provided")
        if not self.default_addr.strip():
            raise ValueError("--default-addr must be provided")
        if self.router_context_chars <= 0:
            raise ValueError("--router-context-chars must be positive")
        if self.router_timeout is not None and (not math.isfinite(self.router_timeout) or self.router_timeout <= 0):
            raise ValueError("--router-timeout must be a finite positive number")
        targets = parse_upstreams(self.upstream)
        settings = RouterSettings(
            targets=targets,
            router_model=router_model.strip(),
            router_base_url=router_base_url.strip(),
            router_api_key=router_api_key,
            default_addr=self.default_addr.strip(),
            router_timeout=self.router_timeout,
            context_chars=self.router_context_chars,
            log_details=self.log_router_details,
        )
        logger.debug(
            f"Router resolved params: model={settings.router_model!r}, base_url={settings.router_base_url!r}, "
            f"upstreams={len(settings.targets)}, default_addr={settings.default_addr!r}, "
            f"api_key={'*' * 8 if settings.router_api_key else '(empty)'}"
        )
        await serve_router(socket_path=self.session_socket, settings=settings)
