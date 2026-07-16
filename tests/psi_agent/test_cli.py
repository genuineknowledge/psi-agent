from __future__ import annotations

import sys
from typing import Any

import pytest

from psi_agent.ai import Ai
from psi_agent.cli import main
from psi_agent.router import Router


def _capture_command(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> object:
    captured: list[object] = []

    def fake_anyio_run(run: Any) -> None:
        captured.append(run.__self__)

    monkeypatch.setattr(sys, "argv", ["psi-agent", *args])
    monkeypatch.setattr("psi_agent.cli.anyio.run", fake_anyio_run)
    main()
    assert len(captured) == 1
    return captured[0]


def test_main_preserves_ordinary_ai_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    command = _capture_command(
        monkeypatch,
        [
            "ai",
            "--session-socket",
            "http://127.0.0.1:8100",
            "--provider",
            "openai",
            "--model",
            "qwen",
        ],
    )
    assert isinstance(command, Ai)
    assert command.provider == "openai"
    assert command.model == "qwen"


def test_main_builds_top_level_router_with_ordered_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    first = '{"socket":"http://a","description":"simple"}'
    second = '{"socket":"http://b","description":"complex"}'
    command = _capture_command(
        monkeypatch,
        [
            "router",
            "--session-socket",
            "http://127.0.0.1:8100",
            "--router-socket",
            "http://a",
            "--upstream",
            first,
            second,
            "--default-socket",
            "http://default",
            "--router-context-chars",
            "8000",
            "--log-router-details",
            "--verbose",
        ],
    )
    assert isinstance(command, Router)
    assert command.upstream == [first, second]
    assert command.default_socket == "http://default"
    assert command.router_context_chars == 8000
    assert command.log_router_details is True
    assert command.verbose is True


def test_router_help_lists_router_options(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["psi-agent", "router", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--upstream" in output
    assert "--default-socket" in output
    assert "--router-context-chars" in output
