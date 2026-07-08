from __future__ import annotations

from dataclasses import dataclass, field

from psi_agent._logging import setup_logging

from .client import run_repl


@dataclass
class ChannelRepl:
    """Interactive REPL channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe)."""

    models: list[str] = field(default_factory=list)
    """Ordered models from simpler/faster to stronger/slower."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        await run_repl(
            session_socket=self.session_socket,
            models=self.models,
        )
