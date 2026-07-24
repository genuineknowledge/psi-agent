"""Tests for the enhanced WEB_SEARCH_RECENCY_SECTION prompt text."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = WORKSPACE_ROOT / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

ps: Any = importlib.import_module("prompt_sections")


def test_section_names_entity_membership_category():
    text = ps.WEB_SEARCH_RECENCY_SECTION
    assert "current status, affiliation, or membership" in text
    # roster / lineup wording present so sports/esports lineups are covered
    assert "roster" in text and "lineup" in text


def test_section_has_default_must_verify_wording():
    text = ps.WEB_SEARCH_RECENCY_SECTION
    assert "default to verifying online" in text
    assert "do not answer from memory" in text


def test_section_has_reverse_boundary():
    text = ps.WEB_SEARCH_RECENCY_SECTION
    assert "Stable facts that do not change over time" in text
    # gray-area tiebreak leans toward searching
    assert "lean toward a quick search" in text
