from __future__ import annotations

import os
from dataclasses import dataclass

from psi_agent.logging import setup_logging

from .server import serve_anthropic_messages


@dataclass
class AnthropicMessages:
    """Start an Anthropic Messages to OpenAI compatible AI backend service.

    Converts OpenAI-format Chat Completions requests to Anthropic Messages API format,
    and converts Anthropic responses (including thinking blocks) back to OpenAI SSE format.
    """

    session_socket: str
    """Path to the Unix domain socket to listen on."""

    model: str
    """Model name to use for upstream requests."""

    base_url: str
    """Base URL of the upstream Anthropic-compatible API."""

    api_key: str = ""
    """API key for the upstream service (x-api-key header). Falls back to ANTHROPIC_API_KEY env var."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        """Start the server and block until cancelled."""
        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        setup_logging(verbose=self.verbose)
        await serve_anthropic_messages(
            socket_path=self.session_socket,
            model=self.model,
            api_key=api_key,
            base_url=self.base_url,
        )
