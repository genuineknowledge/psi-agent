from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .client import run_cli


@dataclass
class ChannelCli:
    """One-shot CLI channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe)."""

    message: str
    """Message to send to the session. Use ``-`` to read the message from stdin."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        await run_cli(session_socket=self.session_socket, message=self.message)
