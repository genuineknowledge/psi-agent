"""Tests for workspace-relative PDF path resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"


def _load_tool() -> ModuleType:
    tool_path = TOOLS_DIR / "read_pdf.py"
    module = ModuleType("haitun_read_pdf_test")
    module.__file__ = str(tool_path)
    original_sys_path = sys.path.copy()
    sys.modules[module.__name__] = module
    try:
        source = tool_path.read_text(encoding="utf-8")
        exec(compile(source, str(tool_path), "exec"), module.__dict__)
    finally:
        sys.path[:] = original_sys_path
        sys.modules.pop(module.__name__, None)
    return module


@pytest.mark.anyio
async def test_read_pdf_resolves_path_from_workspace(monkeypatch) -> None:
    tool = _load_tool()
    workspace = "/srv/haitun-workspace"
    resolved = Path(workspace) / "documents" / "resume.pdf"
    captured: dict[str, object] = {}
    sentinel = object()

    async def fake_read_pdf_impl(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    def fake_dumps_result(result: object) -> str:
        assert result is sentinel
        return "serialized"

    monkeypatch.setattr(tool._paths, "workspace_dir", lambda explicit="": explicit or workspace)
    monkeypatch.setattr(tool._p, "read_pdf_impl", fake_read_pdf_impl)
    monkeypatch.setattr(tool._p, "dumps_result", fake_dumps_result)

    result = await tool.read_pdf(
        "documents/resume.pdf",
        pages="2-3",
        max_pages=5,
        force_ocr=True,
    )

    assert result == "serialized"
    assert captured == {
        "pdf_path": str(resolved),
        "pages": "2-3",
        "max_pages": 5,
        "force_ocr": True,
        "workspace_raw": workspace,
    }
