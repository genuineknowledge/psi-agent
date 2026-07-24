"""Shared fixtures for Haitun agent-package tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def app_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point AppData at *tmp_path* and return ``{tmp}/history`` for JSONL fixtures."""
    monkeypatch.setenv("PSI_APP_DATA_ROOT", str(tmp_path))
    hist = tmp_path / "history"
    hist.mkdir(parents=True, exist_ok=True)
    return hist


@pytest.fixture
def app_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point AppData at *tmp_path* (history/ + todos/ + state/)."""
    monkeypatch.setenv("PSI_APP_DATA_ROOT", str(tmp_path))
    return tmp_path
