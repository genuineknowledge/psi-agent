from __future__ import annotations

from dataclasses import dataclass

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

    api_key: str
    """API key for the upstream service (x-api-key header)."""

    base_url: str
    """Base URL of the upstream Anthropic-compatible API."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        """Start the server and block until cancelled."""
        from psi_agent.logging import setup_logging

        setup_logging(verbose=self.verbose)
        await serve_anthropic_messages(
            socket_path=self.session_socket,
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
        )
