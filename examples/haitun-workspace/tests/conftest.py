"""Isolate AppData root so history/todo dual-read does not touch the real user dir."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_psi_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / ".psi-appdata"))
