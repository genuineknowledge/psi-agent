"""Standalone entry point for the experimental single-target Router."""

from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from ..client import RouterHttpClient
from ..server import serve_router
from .models import RoutingConfig, RoutingTarget
from .selector import RouteSelector
from .strategy import RoutingStrategy


@dataclass
class RoutingRouter:
    """Expose an LLM-selected set of AI services as one local API service."""

    session_socket: str
    selector_socket: str
    targets: list[RoutingTarget]
    selector_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_selection_chars: int = 12_000
    verbose: bool = False

    async def run(self) -> None:
        """Validate configuration and serve until externally cancelled."""

        setup_logging(verbose=self.verbose)
        config = RoutingConfig(
            session_socket=self.session_socket,
            selector_socket=self.selector_socket,
            targets=self.targets,
            selector_timeout=self.selector_timeout,
            target_timeout=self.target_timeout,
            max_selection_chars=self.max_selection_chars,
        )
        client = RouterHttpClient()
        selector = RouteSelector(config=config, client=client)
        strategy = RoutingStrategy(config=config, selector=selector, client=client)
        await serve_router(session_socket=config.session_socket, strategy=strategy)


__all__ = ["RoutingRouter"]
