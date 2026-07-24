"""Tests for sessions_handoff tool."""

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
handoff_tool: Any = importlib.import_module("sessions_handoff")


def test_handoff_tool_metadata() -> None:
    meta = ToolFunction.from_callable(handoff_tool.sessions_handoff)
    assert meta.name == "sessions_handoff"
    assert meta.parameters["required"] == ["target_session_id", "task"]


def test_format_handoff_message() -> None:
    text = session_helpers._format_handoff_message(
        source_session_id="src1",
        task="Continue PR #305",
        context_body="**User:** fix CI",
        query="PR #305",
        source_title="GitHub work",
    )
    assert "src1" in text
    assert "Continue PR #305" in text
    assert "PR #305" in text
    assert "GitHub work" in text


def test_messages_to_context_body_filters_query() -> None:
    messages = [
        {"role": "user", "content": "work on export feature"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "unrelated question"},
    ]
    body = session_helpers._messages_to_context_body(messages, query="export")
    assert "export" in body
    assert "unrelated" not in body


@pytest.mark.anyio
async def test_build_handoff_context_from_history(tmp_path: Path, app_history: Path) -> None:
    (app_history / "src.jsonl").write_text(
        '{"role":"user","content":"start export"}\n{"role":"assistant","content":"done step 1"}\n',
        encoding="utf-8",
    )
    result = await session_helpers.build_handoff_context(
        source_session_id="src",
        workspace_raw=str(tmp_path),
        query="export",
    )
    assert result["ok"] is True
    assert "export" in result["context_body"]


@pytest.mark.anyio
async def test_resolve_source_via_keyword_search(tmp_path: Path, app_history: Path) -> None:
    (app_history / "findme.jsonl").write_text('{"role":"user","content":"PR #305 fix"}\n', encoding="utf-8")

    sid, meta = await session_helpers._resolve_handoff_source_session(
        source_session_id="",
        query="PR #305",
        category="",
        workspace_raw=str(tmp_path),
    )
    assert sid == "findme"
    assert meta is not None


@pytest.mark.anyio
async def test_handoff_requires_target(tmp_path: Path) -> None:
    result = await session_helpers.handoff_session(
        target_session_id="",
        task="continue",
        workspace_raw=str(tmp_path),
    )
    assert result["ok"] is False


@pytest.mark.anyio
async def test_handoff_rejects_same_source_and_target(tmp_path: Path) -> None:
    result = await session_helpers.handoff_session(
        target_session_id="same",
        task="continue",
        source_session_id="same",
        workspace_raw=str(tmp_path),
    )
    assert result["ok"] is False


@pytest.mark.anyio
async def test_handoff_tool_returns_json(tmp_path: Path, app_history: Path) -> None:
    (app_history / "src.jsonl").write_text('{"role":"user","content":"x"}\n', encoding="utf-8")

    raw = await handoff_tool.sessions_handoff(
        target_session_id="tgt",
        task="go",
        source_session_id="src",
        workspace=str(tmp_path),
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["source_session_id"] == "src"
    assert payload["target_session_id"] == "tgt"
