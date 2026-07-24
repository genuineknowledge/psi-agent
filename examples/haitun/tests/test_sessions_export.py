"""Tests for sessions_export tool."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

session_helpers: Any = importlib.import_module("_session_helpers")
export_tool: Any = importlib.import_module("sessions_export")


def test_export_tool_metadata() -> None:
    meta = ToolFunction.from_callable(export_tool.sessions_export)
    assert meta.name == "sessions_export"
    assert meta.parameters["required"] == ["output_path"]


@pytest.mark.anyio
async def test_export_markdown(tmp_path: Path, app_history: Path) -> None:
    (app_history / "exp1.jsonl").write_text(
        '{"role":"user","content":"hello export"}\n{"role":"assistant","content":"done"}\n',
        encoding="utf-8",
    )
    out = tmp_path / "exports" / "chat.md"

    result = await session_helpers.export_session(
        session_id="exp1",
        output_path=str(out),
        export_format="markdown",
        workspace_raw=str(tmp_path),
        include_gateway=False,
    )
    assert result["ok"] is True
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "hello export" in text
    assert "### User" in text
    assert "### Assistant" in text
    assert "Session exp1" not in text


@pytest.mark.anyio
async def test_export_json(tmp_path: Path, app_history: Path) -> None:
    (app_history / "exp2.jsonl").write_text('{"role":"user","content":"x"}\n', encoding="utf-8")
    out = tmp_path / "out.json"

    raw = await export_tool.sessions_export(
        output_path=str(out),
        session_id="exp2",
        export_format="json",
        workspace=str(tmp_path),
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["session_id"] == "exp2"
    assert len(data["messages"]) == 1


@pytest.mark.anyio
async def test_export_requires_session(tmp_path: Path) -> None:
    result = await session_helpers.export_session(
        session_id="",
        output_path=str(tmp_path / "x.md"),
        workspace_raw=str(tmp_path),
    )
    assert result["ok"] is False
