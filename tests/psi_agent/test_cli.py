from __future__ import annotations

import io
import sys

import pytest

from psi_agent import cli
from psi_agent.errors import UserFacingError


class _FakeStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.encoding_value = "gbk"
        self.reconfigured: dict[str, object] = {}

    @property
    def encoding(self) -> str:
        return self.encoding_value

    def reconfigure(self, **kwargs: object) -> None:
        self.reconfigured.update(kwargs)
        self.encoding_value = str(kwargs.get("encoding", self.encoding_value))


def test_configure_console_encoding_forces_utf8(monkeypatch) -> None:
    fake_stdout = _FakeStream()
    fake_stderr = _FakeStream()
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", fake_stderr)

    cli._configure_console_encoding()

    assert fake_stdout.reconfigured["encoding"] == "utf-8"
    assert fake_stdout.reconfigured["errors"] == "replace"
    assert fake_stderr.reconfigured["encoding"] == "utf-8"
    assert fake_stderr.reconfigured["errors"] == "replace"


class _Cmd:
    verbose = False

    async def run(self) -> None:
        return None


class _VerboseCmd:
    verbose = True

    async def run(self) -> None:
        return None


def test_main_prints_user_facing_error(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.tyro, "cli", lambda *_args, **_kwargs: _Cmd())
    monkeypatch.setattr(cli.anyio, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(UserFacingError("Bad setup")))

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "Error: Bad setup" in capsys.readouterr().err


def test_main_prints_keyboard_interrupt(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.tyro, "cli", lambda *_args, **_kwargs: _Cmd())
    monkeypatch.setattr(cli.anyio, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 130
    assert "Interrupted." in capsys.readouterr().err


def test_main_hides_unexpected_error_without_verbose(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.tyro, "cli", lambda *_args, **_kwargs: _Cmd())
    monkeypatch.setattr(cli.anyio, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")))

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "unexpected problem" in err
    assert "secret" not in err


def test_main_reraises_unexpected_error_with_verbose(monkeypatch) -> None:
    monkeypatch.setattr(cli.tyro, "cli", lambda *_args, **_kwargs: _VerboseCmd())
    monkeypatch.setattr(cli.anyio, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("debug")))

    with pytest.raises(RuntimeError, match="debug"):
        cli.main()
