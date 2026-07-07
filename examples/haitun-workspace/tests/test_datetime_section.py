"""Tests for the knowledge-cutoff line in _build_datetime_section."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = WORKSPACE_ROOT / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

system: Any = importlib.import_module("system")


def test_cutoff_line_present_when_env_set(monkeypatch):
    monkeypatch.setenv("HAITUN_KNOWLEDGE_CUTOFF", "2026-01")
    out = system._build_datetime_section()
    assert "Knowledge cutoff: 2026-01" in out
    assert "verify online" in out


def test_cutoff_line_neutral_when_env_unset(monkeypatch):
    monkeypatch.delenv("HAITUN_KNOWLEDGE_CUTOFF", raising=False)
    out = system._build_datetime_section()
    assert "Knowledge cutoff: unknown" in out
    # never fabricate a fake date when unset
    assert "2026-01" not in out


def test_current_date_still_present(monkeypatch):
    monkeypatch.delenv("HAITUN_KNOWLEDGE_CUTOFF", raising=False)
    out = system._build_datetime_section()
    assert "## Current Date & Time" in out
    assert "Date:" in out
