"""CLI entry point for the serial Router service."""

from __future__ import annotations

import math
from dataclasses import dataclass

from psi_agent._logging import setup_logging
from psi_agent.router.client import RouterClient
from psi_agent.router.orchestrator import Orchestrator
from psi_agent.router.planner import Planner
from psi_agent.router.protocol import RouterConfig
from psi_agent.router.server import serve_router


@dataclass
class Router:
    """Start the Router service used between Session and configured AI backends."""

    session_socket: str
    """Transport address on which Router accepts Session requests."""

    router_socket: str
    """Transport address for the planning and aggregation AI backend."""

    default_socket: str
    """Transport address for fallback requests after an orchestration failure."""

    upstream: list[tuple[str, str]]
    """Configured (transport address, capability description) branch backends."""

    max_tool_rounds: int = 10
    """Maximum tool rounds independently allowed for each branch."""

    router_timeout: float | None = 60.0
    """Timeout in seconds for planning requests, or None to disable it."""

    branch_timeout: float | None = None
    """Timeout in seconds for each branch request, or None to disable it."""

    aggregate_timeout: float | None = None
    """Timeout in seconds for the final aggregation request, or None to disable it."""

    run_ttl: float = 1_800.0
    """Maximum seconds to retain a run while waiting for tool results."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        """Validate configuration and serve until externally cancelled."""

        setup_logging(verbose=self.verbose)
        config = RouterConfig(
            session_socket=self.session_socket,
            router_socket=self.router_socket,
            default_socket=self.default_socket,
            upstream=self.upstream,
            max_tool_rounds=self.max_tool_rounds,
            router_timeout=self.router_timeout,
            branch_timeout=self.branch_timeout,
            aggregate_timeout=self.aggregate_timeout,
            run_ttl=self.run_ttl,
        )
        for name, timeout in (
            ("router_timeout", config.router_timeout),
            ("branch_timeout", config.branch_timeout),
            ("aggregate_timeout", config.aggregate_timeout),
            ("run_ttl", config.run_ttl),
        ):
            if timeout is not None and not math.isfinite(timeout):
                raise ValueError(f"{name} must be a finite positive number or None")

        client = RouterClient()
        planner = Planner(
            client=client,
            router_socket=config.router_socket,
            upstream=config.upstream,
            timeout=config.router_timeout,
        )
        orchestrator = Orchestrator(config=config, client=client, planner=planner)
        await serve_router(config=config, orchestrator=orchestrator)


__all__ = ["Router"]
