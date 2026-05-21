from __future__ import annotations

from dataclasses import dataclass

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

    async def run(self) -> None:
        from psi_agent.logging import setup_logging

        setup_logging(verbose=self.verbose)
        await run_cli(session_socket=self.session_socket, message=self.message)
