"""AI backend — unified multi-provider LLM client served over a Unix socket."""

from __future__ import annotations

import os
from dataclasses import dataclass

from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._keys import API_KEY_KEY, BASE_URL_KEY, MODEL_KEY, PROVIDER_KEY
from psi_agent._logging import setup_logging
from psi_agent._sockets import serve_app

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

    app = web.Application()
    app[PROVIDER_KEY] = provider
    app[MODEL_KEY] = model
    app[API_KEY_KEY] = api_key
    app[BASE_URL_KEY] = base_url
    app.router.add_post("/chat/completions", handler)

    await serve_app(app, socket_path)


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
