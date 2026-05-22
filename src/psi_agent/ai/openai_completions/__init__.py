from __future__ import annotations

import os
from dataclasses import dataclass

from psi_agent.logging import setup_logging

from .server import serve_openai_completions


@dataclass
class OpenAICompletions:
    """Start an OpenAI-compatible AI backend service.

    Proxies OpenAI Chat Completions requests, injecting the configured model and API key.
    """

    session_socket: str
    """Path to the Unix domain socket to listen on."""

    model: str
    """Model name to use for upstream requests."""

    api_key: str = ""
    """API key for the upstream service. Falls back to OPENAI_API_KEY env var."""

    base_url: str = "https://api.openai.com/v1"
    """Base URL of the upstream OpenAI-compatible API."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        """Start the server and block until cancelled."""
        api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        setup_logging(verbose=self.verbose)
        await serve_openai_completions(
            socket_path=self.session_socket,
            model=self.model,
            api_key=api_key,
            base_url=self.base_url,
        )
