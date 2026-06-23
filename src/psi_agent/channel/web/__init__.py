from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .server import serve_web_channel


@dataclass
class ChannelWeb:
    """Browser-based chat channel (web front-end for the REPL)."""

    session_socket: str
    """Path to the session Unix domain socket."""

    listen: str = "http://127.0.0.1:8765"
    """HTTP endpoint to serve the web UI on."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        await serve_web_channel(session_socket=self.session_socket, listen=self.listen)
