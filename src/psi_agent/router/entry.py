"""Unified Router entry point for routing and broadcast aggregation."""

from __future__ import annotations

from dataclasses import dataclass

from psi_agent._logging import setup_logging

from .aggregation import AggregationConfig, AggregationStrategy
from .client import RouterHttpClient
from .models import RouterMode, RouterTarget
from .routing import RouteSelector, RoutingConfig, RoutingStrategy
from .server import RouterStrategy, serve_router


@dataclass
class Router:
    """Expose one explicitly selected Router mode through a shared facade."""

    session_socket: str
    router_socket: str
    mode: RouterMode | str
    upstream: list[tuple[str, str]]
    router_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_context_chars: int = 12_000
    verbose: bool = False

    async def run(self) -> None:
        """Build the selected strategy and serve until externally cancelled."""

        setup_logging(verbose=self.verbose)
        try:
            mode = RouterMode(self.mode)
        except TypeError, ValueError:
            raise ValueError("mode must be 'routing' or 'aggregation'") from None
        if not isinstance(self.upstream, list) or not self.upstream:
            raise ValueError("upstream must be a non-empty list of two-string tuples")
        if any(
            not isinstance(item, tuple) or len(item) != 2 or any(not isinstance(value, str) for value in item)
            for item in self.upstream
        ):
            raise ValueError("upstream must be a non-empty list of two-string tuples")

        targets = [
            RouterTarget(
                candidate_id=f"candidate-{index}",
                socket=socket,
                description=description,
            )
            for index, (socket, description) in enumerate(self.upstream, start=1)
        ]
        client = RouterHttpClient()
        if mode is RouterMode.ROUTING:
            config = RoutingConfig(
                session_socket=self.session_socket,
                selector_socket=self.router_socket,
                targets=targets,
                selector_timeout=self.router_timeout,
                target_timeout=self.target_timeout,
                max_selection_chars=self.max_context_chars,
            )
            selector = RouteSelector(config=config, client=client)
            strategy: RouterStrategy = RoutingStrategy(
                config=config,
                selector=selector,
                client=client,
            )
        else:
            config = AggregationConfig(
                session_socket=self.session_socket,
                aggregator_socket=self.router_socket,
                targets=targets,
                aggregator_timeout=self.router_timeout,
                target_timeout=self.target_timeout,
                max_context_chars=self.max_context_chars,
            )
            strategy = AggregationStrategy(config=config, client=client)
        await serve_router(session_socket=config.session_socket, strategy=strategy)


__all__ = ["Router"]
