from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .client import run_repl


@dataclass
class ChannelRepl:
    """Interactive REPL channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe)."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        await run_repl(session_socket=self.session_socket)
