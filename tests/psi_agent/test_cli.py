from __future__ import annotations

import tyro

from psi_agent.cli import Command
from psi_agent.router import Router


def test_router_subcommand_parses_timeouts_and_context_budget() -> None:
    command = tyro.cli(
        Command,
        args=[
            "router",
            "--session-socket",
            "router.sock",
            "--router-socket",
            "aggregate.sock",
            "--mode",
            "aggregation",
            "--upstream",
            "one.sock",
            "coding",
            "two.sock",
            "research",
            "--router-timeout",
            "30",
            "--target-timeout",
            "8",
            "--max-context-chars",
            "9000",
        ],
    )

    assert isinstance(command, Router)
    assert command.mode == "aggregation"
    assert command.upstream == [("one.sock", "coding"), ("two.sock", "research")]
    assert (command.router_timeout, command.target_timeout, command.max_context_chars) == (30, 8, 9000)
    assert not hasattr(command, "default_socket")
    assert not hasattr(command, "max_context_length")
