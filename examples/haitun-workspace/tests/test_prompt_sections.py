"""Regression tests for high-risk workspace prompt guidance."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

SYSTEMS_DIR = Path(__file__).resolve().parents[1] / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

sections = importlib.import_module("prompt_sections")


def test_document_guidance_uses_existing_tools_without_runtime_install() -> None:
    combined = sections.SEND_FILES_SECTION + sections.DELIVERABLES_AS_FILES_SECTION

    assert "Do not run pip install" in combined
    assert "call `write_word`" in combined
    assert "install a library" not in combined


def test_long_structured_deliverables_are_file_first() -> None:
    assert "do not draft the full artifact in chat first" in sections.DELIVERABLES_AS_FILES_SECTION
