from __future__ import annotations

import pytest

from psi_agent.ai import Ai
from psi_agent.cli import parse_command
from psi_agent.router import AiRouter


def test_parse_command_preserves_ordinary_ai_shape() -> None:
    command = parse_command(
        [
            "ai",
            "--session-socket",
            "http://127.0.0.1:8100",
            "--provider",
            "openai",
            "--model",
            "qwen",
        ]
    )
    assert isinstance(command, Ai)
    assert command.provider == "openai"
    assert command.model == "qwen"


def test_parse_command_builds_router_with_ordered_upstreams() -> None:
    first = '{"model_name":"qwen","addr":"http://a","description":"simple"}'
    second = '{"model_name":"deepseek","addr":"http://b","description":"complex"}'
    command = parse_command(
        [
            "ai",
            "router",
            "--session-socket",
            "http://127.0.0.1:8100",
            "--router-model",
            "route-model",
            "--router-base-url",
            "http://router/v1",
            "--upstream",
            first,
            second,
            "--default-addr",
            "http://default",
            "--router-context-chars",
            "8000",
            "--log-router-details",
            "--verbose",
        ]
    )
    assert isinstance(command, AiRouter)
    assert command.upstream == [first, second]
    assert command.default_addr == "http://default"
    assert command.router_context_chars == 8000
    assert command.log_router_details is True
    assert command.verbose is True


def test_router_help_lists_router_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_command(["ai", "router", "--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--upstream" in output
    assert "--default-addr" in output
    assert "--router-context-chars" in output
