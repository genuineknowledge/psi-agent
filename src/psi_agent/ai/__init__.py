"""AI backend — unified multi-provider LLM client served over a Unix socket."""

from __future__ import annotations

import os
from dataclasses import dataclass

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._socket import serve_app



PROVIDER_KEY: web.AppKey[str] = web.AppKey("psi_agent.ai.provider")
MODEL_KEY: web.AppKey[str] = web.AppKey("psi_agent.ai.model")
API_KEY_KEY: web.AppKey[str] = web.AppKey("psi_agent.ai.api_key")
BASE_URL_KEY: web.AppKey[str] = web.AppKey("psi_agent.ai.base_url")


async def serve_ai(
    *,
    socket_path: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    """Serve an AI backend on a Unix socket."""

    logger.info(f"Starting AI service on {socket_path} (model={model}, base_url={base_url})")

    app = web.Application()
    app[PROVIDER_KEY] = provider
    app[MODEL_KEY] = model
    app[API_KEY_KEY] = api_key
    app[BASE_URL_KEY] = base_url
    from .server import handle_chat_completions
    app.router.add_post("/chat/completions", handle_chat_completions)

    logger.info(f"AI listening on {socket_path}")
    try:
        await serve_app(app, socket_path)
    finally:
        logger.info(f"Shutting down AI on {socket_path}")


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
        await serve_ai(
            socket_path=self.session_socket,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
