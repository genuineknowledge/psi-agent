"""AI backend — unified multi-provider LLM client served over a Unix socket."""

from __future__ import annotations

import os
from dataclasses import dataclass

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site

from .server import handle_chat_completions


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

    api_key_status = "set" if api_key else "empty"
    logger.info(
        f"Starting AI service on {socket_path} "
        f"(provider={provider!r}, model={model!r}, base_url={base_url}, api_key={api_key_status})"
    )

    # Large conversation contexts (long histories, tool outputs) routinely exceed
    # aiohttp's 1 MiB default body limit, which would reject the request with
    # HTTPRequestEntityTooLarge before it ever reaches the upstream. Match the
    # gateway app's 100 MiB ceiling so the forwarder accepts the same payloads.
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app["provider"] = provider
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, socket_path)
        await site.start()
    except Exception as e:
        logger.error(f"Failed to start AI service on {socket_path}: {e}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    logger.info(f"AI listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down AI on {socket_path}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        logger.info(f"AI shutdown complete on {socket_path}")


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
        setup_logging(verbose=self.verbose)
        provider = self.provider or os.environ.get("PSI_AI_PROVIDER", "")
        model = self.model or os.environ.get("PSI_AI_MODEL", "")
        api_key = self.api_key or os.environ.get("PSI_AI_API_KEY", "")
        base_url = self.base_url or os.environ.get("PSI_AI_BASE_URL", "")
        logger.debug(
            f"AI resolved params: provider={provider!r}, model={model!r}, "
            f"base_url={base_url!r}, api_key={'*' * 8 if api_key else '(empty)'}"
        )
        await serve_ai(
            socket_path=self.session_socket,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            handler=handle_chat_completions,
        )
