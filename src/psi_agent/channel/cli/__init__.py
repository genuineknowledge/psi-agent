from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .client import run_cli


@dataclass
class ChannelCli:
    """One-shot CLI channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    message: str
    """Message to send to the session."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    show_reasoning: bool = False
    """Print reasoning and tool trace chunks."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        await run_cli(
            session_socket=self.session_socket,
            message=self.message,
            show_reasoning=self.show_reasoning,
        )
