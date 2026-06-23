from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .server import AgentRoutes, serve_web_channel


@dataclass
class ChannelWeb:
    """Browser-based chat channel (web front-end for the REPL)."""

    session_socket: str
    """Default session endpoint (used when a route-specific one is not set).

    A path to a Unix domain socket, or an ``http://host:port/v1`` TCP endpoint.
    """

    listen: str = "http://127.0.0.1:8765"
    """HTTP endpoint to serve the web UI on."""

    upload_dir: str = ""
    """Directory for uploaded files. Falls back to PSI_WEB_UPLOAD_DIR or ~/.psi-agent/channel-web/uploads."""

    # Demo routing: the web UI sends `modules.flow` / `modules.security` and the
    # channel forwards to one of four agents by (flow, security). Each value is a
    # session endpoint (unix socket path or http://host:port/v1). When unset, the
    # request falls back to `session_socket`.
    fusion_offguard: str = ""
    """flow=on, security=off  →  examples/fusion-offguard"""

    fusion_onguard: str = ""
    """flow=on, security=on   →  examples/fusion-onguard"""

    hermes_offguard: str = ""
    """flow=off, security=off  →  examples/hermes-offguard (Hermes compare baseline)"""

    hermes_onguard: str = ""
    """flow=off, security=on   →  examples/hermes-onguard"""

    frontend_dist: str = ""
    """Path to the built Vue frontend (dist/). Defaults to ./frontend/dist next to the web module."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        routes = AgentRoutes(
            default=self.session_socket,
            fusion_offguard=self.fusion_offguard or self.session_socket,
            fusion_onguard=self.fusion_onguard or self.session_socket,
            hermes_offguard=self.hermes_offguard or self.session_socket,
            hermes_onguard=self.hermes_onguard or self.session_socket,
        )
        await serve_web_channel(
            routes=routes, listen=self.listen, upload_dir=self.upload_dir, frontend_dist=self.frontend_dist
        )
