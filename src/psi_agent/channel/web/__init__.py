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

    upload_dir: str = ""
    """Directory for uploaded files. Falls back to PSI_WEB_UPLOAD_DIR or ~/.psi-agent/channel-web/uploads."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        await serve_web_channel(session_socket=self.session_socket, listen=self.listen, upload_dir=self.upload_dir)
