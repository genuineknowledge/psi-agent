"""Unified Router entry point for routing, aggregation, and fallback."""

from __future__ import annotations

from dataclasses import dataclass, field

from psi_agent._logging import setup_logging

from .aggregation import AggregationConfig, AggregationStrategy
from .client import RouterHttpClient
from .fallback import FallbackConfig, FallbackStrategy
from .models import RouterBackendType, RouterMode, RouterTarget, RouterUpstream
from .routing import RouteSelector, RoutingConfig, RoutingStrategy
from .server import RouterStrategy, serve_router


@dataclass
class Router:
    """Expose one explicitly selected Router mode through a shared facade."""

    session_socket: str
    router_socket: str | None
    mode: RouterMode | str
    upstream: list[RouterUpstream]
    upstream_types: list[RouterBackendType] = field(default_factory=list)
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
            raise ValueError("mode must be 'routing', 'aggregation', or 'fallback'") from None
        if not isinstance(self.upstream, list) or not self.upstream:
            raise ValueError("upstream must be a non-empty list of two- or three-string tuples")
        if not isinstance(self.upstream_types, list) or any(
            not isinstance(value, str) or value not in {"ai", "router"} for value in self.upstream_types
        ):
            raise ValueError("upstream_types must contain only 'ai' or 'router'")
        if self.upstream_types and len(self.upstream_types) != len(self.upstream):
            raise ValueError("upstream_types must have the same length as upstream")

        targets: list[RouterTarget] = []
        for index, item in enumerate(self.upstream, start=1):
            if not isinstance(item, tuple) or len(item) not in {2, 3}:
                raise ValueError("upstream must be a non-empty list of two- or three-string tuples")
            if any(not isinstance(value, str) for value in item):
                raise ValueError("upstream must be a non-empty list of two- or three-string tuples")
            if self.upstream_types and len(item) != 2:
                raise ValueError("three-item upstreams cannot be combined with upstream_types")
            backend_type: RouterBackendType = self.upstream_types[index - 1] if self.upstream_types else "ai"
            if not self.upstream_types and len(item) == 3:
                raw_backend_type = item[2]
                if raw_backend_type not in {"ai", "router"}:
                    raise ValueError("upstream backend type must be 'ai' or 'router'")
                backend_type = raw_backend_type
            targets.append(
                RouterTarget(
                    candidate_id=f"candidate-{index}",
                    socket=item[0],
                    description=item[1],
                    backend_type=backend_type,
                )
            )

        client = RouterHttpClient()
        if mode is RouterMode.ROUTING:
            if not isinstance(self.router_socket, str) or not self.router_socket.strip():
                raise ValueError("routing mode requires a non-empty router_socket")
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
            session_socket = config.session_socket
        elif mode is RouterMode.AGGREGATION:
            if not isinstance(self.router_socket, str) or not self.router_socket.strip():
                raise ValueError("aggregation mode requires a non-empty router_socket")
            config = AggregationConfig(
                session_socket=self.session_socket,
                aggregator_socket=self.router_socket,
                targets=targets,
                aggregator_timeout=self.router_timeout,
                target_timeout=self.target_timeout,
                max_context_chars=self.max_context_chars,
            )
            strategy = AggregationStrategy(config=config, client=client)
            session_socket = config.session_socket
        else:
            if self.router_socket is not None:
                raise ValueError("fallback mode requires router_socket=None")
            fallback_config = FallbackConfig(
                session_socket=self.session_socket,
                targets=targets,
                target_timeout=self.target_timeout,
            )
            strategy = FallbackStrategy(config=fallback_config, client=client)
            session_socket = fallback_config.session_socket
        await serve_router(session_socket=session_socket, strategy=strategy)


__all__ = ["Router"]
