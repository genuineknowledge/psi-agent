"""AI backend — unified multi-provider LLM client served over a Unix socket."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._logging import setup_logging

from .server import handle_chat_completions


def _build_site(runner: web.AppRunner, socket_path: str) -> web.BaseSite:
    """Resolve a listen address into the matching aiohttp site.

    Mirrors ``SessionAgent._build_connector`` so the server side accepts the
    same three address forms the client can dial:
    - ``http://host:port`` / ``https://...`` -> TCP port           (TCPSite)
    - ``npipe://`` / ``\\\\.\\pipe\\`` path     -> Windows named pipe (NamedPipeSite)
    - anything else (a filesystem path)       -> Unix domain socket (UnixSite)
    """
    if socket_path.startswith(("http://", "https://")):
        parsed = urlparse(socket_path)
        return web.TCPSite(runner, host=parsed.hostname or "127.0.0.1", port=parsed.port or 8000)
    if socket_path.startswith("npipe://") or socket_path.startswith("\\\\.\\pipe\\"):
        return web.NamedPipeSite(runner, socket_path.removeprefix("npipe://"))
    return web.UnixSite(runner, socket_path)


async def serve_ai(
    *,
    socket_path: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    handler: Handler,
) -> None:
    """Serve an AI backend on a Unix socket."""

    logger.info(f"Starting AI service on {socket_path} (model={model}, base_url={base_url})")

    app = web.Application()
    app["provider"] = provider
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = _build_site(runner, socket_path)
    await site.start()

    logger.info(f"AI listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down AI on {socket_path}")
        await runner.cleanup()


@dataclass
class Ai:
    """Start an AI backend service that forwards to any LLM provider."""

    session_socket: str
    """Path to the Unix domain socket to listen on."""

    provider: str = ""
    """Provider key (openai, anthropic, gemini, etc.). Falls back to PSI_AI_PROVIDER env var."""

    model: str = ""
    """Model name. Falls back to PSI_AI_MODEL env var."""

    api_key: str = ""
    """API key for the upstream service. Falls back to PSI_AI_API_KEY env var."""

    base_url: str = ""
    """Base URL of the upstream API. Falls back to PSI_AI_BASE_URL env var."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        """Start the server and block until cancelled."""
        provider = self.provider or os.environ.get("PSI_AI_PROVIDER", "")
        model = self.model or os.environ.get("PSI_AI_MODEL", "")
        api_key = self.api_key or os.environ.get("PSI_AI_API_KEY", "")
        base_url = self.base_url or os.environ.get("PSI_AI_BASE_URL", "")
        setup_logging(verbose=self.verbose)
        await serve_ai(
            socket_path=self.session_socket,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            handler=handle_chat_completions,
        )
