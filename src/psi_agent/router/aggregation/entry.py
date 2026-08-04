"""Standalone entry point for broadcast aggregation."""

from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from ..client import RouterHttpClient
from ..models import RouterTarget
from ..server import serve_router
from .models import AggregationConfig
from .strategy import AggregationStrategy


@dataclass
class AggregationRouter:
    """Expose all configured candidates through one dedicated Aggregator."""

    session_socket: str
    aggregator_socket: str
    targets: list[RouterTarget]
    aggregator_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_context_chars: int = 12_000
    verbose: bool = False

    async def run(self) -> None:
        """Validate configuration and serve until externally cancelled."""

        setup_logging(verbose=self.verbose)
        config = AggregationConfig(
            session_socket=self.session_socket,
            aggregator_socket=self.aggregator_socket,
            targets=self.targets,
            aggregator_timeout=self.aggregator_timeout,
            target_timeout=self.target_timeout,
            max_context_chars=self.max_context_chars,
        )
        client = RouterHttpClient()
        strategy = AggregationStrategy(config=config, client=client)
        await serve_router(session_socket=config.session_socket, strategy=strategy)


__all__ = ["AggregationRouter"]
