from __future__ import annotations

import io
import sys

from psi_agent import cli


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
