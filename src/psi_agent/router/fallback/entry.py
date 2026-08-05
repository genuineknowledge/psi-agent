"""Standalone entry point for serial fallback routing."""

from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from ..client import RouterHttpClient
from ..models import RouterTarget
from ..server import serve_router
from .models import FallbackConfig
from .strategy import FallbackStrategy


@dataclass
class FallbackRouter:
    """Expose ordered AI or Router candidates as one resilient local service."""

    session_socket: str
    targets: list[RouterTarget]
    target_timeout: float | None = None
    verbose: bool = False

    async def run(self) -> None:
        """Validate configuration and serve until externally cancelled."""

        setup_logging(verbose=self.verbose)
        config = FallbackConfig(
            session_socket=self.session_socket,
            targets=self.targets,
            target_timeout=self.target_timeout,
        )
        client = RouterHttpClient()
        strategy = FallbackStrategy(config=config, client=client)
        await serve_router(session_socket=config.session_socket, strategy=strategy)


__all__ = ["FallbackRouter"]
