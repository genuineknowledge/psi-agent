"""Unified Router entry point for routing, aggregation, and fallback."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from psi_agent._logging import setup_logging

from .aggregation import AggregationConfig, AggregationStrategy
from .client import RouterHttpClient
from .fallback import FallbackConfig, FallbackStrategy
from .models import RouterBackendType, RouterMode, RouterTarget, RouterUpstream, normalize_request_overrides
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
    require_all_targets: bool = False
    control_request_overrides: dict[str, Any] = field(default_factory=dict)
    target_request_overrides: dict[str, Any] = field(default_factory=dict)
    candidate_request_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_timeouts: dict[str, float] = field(default_factory=dict)
    verbose: bool = False

    async def run(self) -> None:
        """Build the selected strategy and serve until externally cancelled."""

        setup_logging(verbose=self.verbose)
        try:
            mode = RouterMode(self.mode)
        except TypeError, ValueError:
            raise ValueError("mode must be 'routing', 'aggregation', or 'fallback'") from None
        if not isinstance(self.require_all_targets, bool):
            raise ValueError("require_all_targets must be a boolean")
        if mode is not RouterMode.AGGREGATION and self.require_all_targets:
            raise ValueError("require_all_targets is only valid in aggregation mode")
        if not isinstance(self.upstream, list) or not self.upstream:
            raise ValueError("upstream must be a non-empty list of two- or three-string tuples")
        if not isinstance(self.upstream_types, list) or any(
            not isinstance(value, str) or value not in {"ai", "router"} for value in self.upstream_types
        ):
            raise ValueError("upstream_types must contain only 'ai' or 'router'")
        if self.upstream_types and len(self.upstream_types) != len(self.upstream):
            raise ValueError("upstream_types must have the same length as upstream")

        control_request_overrides = normalize_request_overrides(
            value=self.control_request_overrides,
            label="control_request_overrides",
        )
        target_request_overrides = normalize_request_overrides(
            value=self.target_request_overrides,
            label="target_request_overrides",
        )
        if not isinstance(self.candidate_request_overrides, dict) or any(
            not isinstance(candidate_id, str) or not candidate_id for candidate_id in self.candidate_request_overrides
        ):
            raise ValueError("candidate_request_overrides must map candidate IDs to objects")
        if not isinstance(self.candidate_timeouts, dict) or any(
            not isinstance(candidate_id, str)
            or not candidate_id
            or not isinstance(timeout, int | float)
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            for candidate_id, timeout in self.candidate_timeouts.items()
        ):
            raise ValueError("candidate_timeouts must map candidate IDs to finite positive numbers")
        expected_candidate_ids = {f"candidate-{index}" for index in range(1, len(self.upstream) + 1)}
        unknown_candidate_ids = (
            set(self.candidate_request_overrides) | set(self.candidate_timeouts)
        ) - expected_candidate_ids
        if unknown_candidate_ids:
            names = ", ".join(sorted(unknown_candidate_ids))
            raise ValueError(f"candidate_request_overrides contains unknown candidate ID(s): {names}")
        normalized_candidate_overrides = {
            candidate_id: normalize_request_overrides(
                value=value,
                label=f"candidate_request_overrides[{candidate_id!r}]",
            )
            for candidate_id, value in self.candidate_request_overrides.items()
        }

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
            request_overrides = {
                **target_request_overrides,
                **normalized_candidate_overrides.get(f"candidate-{index}", {}),
            }
            targets.append(
                RouterTarget(
                    candidate_id=f"candidate-{index}",
                    socket=item[0],
                    description=item[1],
                    backend_type=backend_type,
                    timeout=self.candidate_timeouts.get(f"candidate-{index}"),
                    request_overrides=request_overrides,
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
                selector_request_overrides=control_request_overrides,
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
                require_all_targets=self.require_all_targets,
                aggregator_request_overrides=control_request_overrides,
            )
            strategy = AggregationStrategy(config=config, client=client)
            session_socket = config.session_socket
        else:
            if self.router_socket is not None:
                raise ValueError("fallback mode requires router_socket=None")
            if control_request_overrides:
                raise ValueError("fallback mode does not have a control model to override")
            fallback_config = FallbackConfig(
                session_socket=self.session_socket,
                targets=targets,
                target_timeout=self.target_timeout,
            )
            strategy = FallbackStrategy(config=fallback_config, client=client)
            session_socket = fallback_config.session_socket
        await serve_router(session_socket=session_socket, strategy=strategy)


__all__ = ["Router"]
