from __future__ import annotations

import tyro

from psi_agent.cli import Command
from psi_agent.router import Router


def test_router_subcommand_parses_native_upstream_tuples() -> None:
    command = tyro.cli(
        Command,
        args=[
            "router",
            "--session-socket",
            "router.sock",
            "--router-socket",
            "planner.sock",
            "--default-socket",
            "default.sock",
            "--upstream",
            "research.sock",
            "research",
            "analysis.sock",
            "structured analysis",
        ],
    )

    assert isinstance(command, Router)
    assert command.upstream == [("research.sock", "research"), ("analysis.sock", "structured analysis")]
