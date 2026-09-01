"""Isolate AppData root so history/todo dual-read does not touch the real user dir."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_psi_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / ".psi-appdata"))


@pytest.fixture
def _todo_appdata(_isolate_psi_appdata: None) -> Path:
    """The isolated AppData root that todo writes land in.

    Resolved the same way ``resolve_appdata_root()`` does (``PSI_APPDATA`` → absolute),
    so tests can rebuild a write path with ``appdata_todo*_path(str(fixture), sid)``.
    Depends on ``_isolate_psi_appdata`` explicitly: the env var must be set first.
    """
    return Path(os.environ["PSI_APPDATA"]).resolve()
