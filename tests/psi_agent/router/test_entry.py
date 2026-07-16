from __future__ import annotations

from psi_agent.router import Router, parse_router_argv


def test_parse_router_argv_returns_router_command() -> None:
    command = parse_router_argv(
        [
            "ai",
            "router",
            "--session-socket",
            "router.sock",
            "--router-model",
            "router-small",
            "--router-base-url",
            "https://router.example/v1",
            "--upstream",
            '{"addr":"http://127.0.0.1:7001","model_name":"qwen","description":"general"}',
        ]
    )

    assert isinstance(command, Router)


def test_parse_router_argv_ignores_non_router_commands() -> None:
    assert parse_router_argv(["gateway", "--verbose"]) is None
