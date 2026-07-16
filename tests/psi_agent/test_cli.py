from __future__ import annotations

import pytest

import psi_agent.cli as cli_module


def test_main_uses_router_parser_for_ai_router(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCommand:
        async def run(self) -> None:
            return None

    observed: dict[str, object] = {}

    def fake_parse(argv: list[str]) -> FakeCommand | None:
        observed["argv"] = argv
        return FakeCommand()

    def fake_tyro_cli(*args: object, **kwargs: object) -> object:
        pytest.fail("tyro.cli should not be called when router parser handles argv")

    def fake_anyio_run(func: object) -> None:
        observed["run"] = func

    monkeypatch.setattr(cli_module.sys, "argv", ["psi-agent", "ai", "router", "--session-socket", "router.sock"])
    monkeypatch.setattr(cli_module, "parse_router_argv", fake_parse)
    monkeypatch.setattr(cli_module.tyro, "cli", fake_tyro_cli)
    monkeypatch.setattr(cli_module.anyio, "run", fake_anyio_run)

    cli_module.main()

    assert observed["argv"] == ["ai", "router", "--session-socket", "router.sock"]
    assert callable(observed["run"])


def test_main_falls_back_to_tyro_for_non_router_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCommand:
        async def run(self) -> None:
            return None

    observed: dict[str, object] = {}

    def fake_parse(argv: list[str]) -> None:
        observed["argv"] = argv
        return None

    def fake_tyro_cli(command_type: object, *, args: list[str]) -> FakeCommand:
        observed["command_type"] = command_type
        observed["tyro_args"] = args
        return FakeCommand()

    def fake_anyio_run(func: object) -> None:
        observed["run"] = func

    monkeypatch.setattr(cli_module.sys, "argv", ["psi-agent", "gateway", "--verbose"])
    monkeypatch.setattr(cli_module, "parse_router_argv", fake_parse)
    monkeypatch.setattr(cli_module.tyro, "cli", fake_tyro_cli)
    monkeypatch.setattr(cli_module.anyio, "run", fake_anyio_run)

    cli_module.main()

    assert observed["argv"] == ["gateway", "--verbose"]
    assert observed["tyro_args"] == ["gateway", "--verbose"]
    assert callable(observed["run"])
