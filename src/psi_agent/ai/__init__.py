"""AI backend — unified multi-provider LLM client served over a Unix socket."""

from __future__ import annotations

import os
from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .common import serve_ai_backend
from .server import handle_chat_completions


@dataclass
class AiBackend:
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
        await serve_ai_backend(
            socket_path=self.session_socket,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            name="ai",
            handler=handle_chat_completions,
        )
