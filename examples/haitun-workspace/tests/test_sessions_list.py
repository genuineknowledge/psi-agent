"""Tests for the Haitun workspace ``sessions_list`` tool."""

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

sessions_list_tool: Any = importlib.import_module("sessions_list")
session_helpers: Any = importlib.import_module("_session_helpers")


def test_tool_metadata_is_loadable() -> None:
    meta = ToolFunction.from_callable(sessions_list_tool.sessions_list)
    assert meta.name == "sessions_list"
    props = meta.parameters["properties"]
    assert set(props) == {"workspace", "running_only", "include_gateway"}


@pytest.mark.anyio
async def test_lists_history_sessions(tmp_path: Path) -> None:
    histories = tmp_path / "histories"
    histories.mkdir()
    history_file = histories / "alpha.jsonl"
    history_file.write_text(
        '{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}\n',
        encoding="utf-8",
    )

    result = await session_helpers.list_sessions(workspace_raw=str(tmp_path))
    assert result["ok"] is True
    assert result["count"] == 1
    session = result["sessions"][0]
    assert session["session_id"] == "alpha"
    assert session["message_count"] == 2
    assert "history" in session["sources"]


@pytest.mark.anyio
async def test_running_only_filters_history_only_rows(tmp_path: Path) -> None:
    histories = tmp_path / "histories"
    histories.mkdir()
    (histories / "idle.jsonl").write_text('{"role":"user","content":"x"}\n', encoding="utf-8")

    result = await session_helpers.list_sessions(
        workspace_raw=str(tmp_path),
        running_only=True,
        include_gateway=False,
    )
    assert result["ok"] is True
    assert result["count"] == 0


@pytest.mark.anyio
async def test_sessions_list_returns_json(tmp_path: Path) -> None:
    histories = tmp_path / "histories"
    histories.mkdir()
    (histories / "beta.jsonl").write_text('{"role":"user","content":"q"}\n', encoding="utf-8")

    raw = await sessions_list_tool.sessions_list(
        workspace=str(tmp_path),
        include_gateway=False,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["sessions"][0]["session_id"] == "beta"


@pytest.mark.anyio
async def test_session_status_for_history_session(tmp_path: Path) -> None:
    histories = tmp_path / "histories"
    histories.mkdir()
    (histories / "gamma.jsonl").write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")

    result = await session_helpers.get_session_status(
        session_id="gamma",
        workspace_raw=str(tmp_path),
        include_gateway=False,
    )
    assert result["ok"] is True
    assert result["session_id"] == "gamma"
    assert result["session"]["message_count"] == 1
    assert "history" in result["session"]["sources"]


@pytest.mark.anyio
async def test_session_history_returns_tail_messages(tmp_path: Path) -> None:
    histories = tmp_path / "histories"
    histories.mkdir()
    lines = [
        '{"role":"user","content":"one"}',
        '{"role":"assistant","content":"two"}',
        '{"role":"tool","name":"bash","content":"ok"}',
        '{"role":"assistant","content":"three"}',
    ]
    (histories / "delta.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = await session_helpers.get_session_history(
        session_id="delta",
        workspace_raw=str(tmp_path),
        limit=2,
        include_gateway=False,
    )
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["messages"][-1]["content"] == "three"

    with_tools = await session_helpers.get_session_history(
        session_id="delta",
        workspace_raw=str(tmp_path),
        limit=10,
        include_tool_messages=True,
        include_gateway=False,
    )
    assert with_tools["count"] == 4
    assert with_tools["messages"][2]["role"] == "tool"


@pytest.mark.anyio
async def test_inspection_tools_metadata() -> None:
    history_tool: Any = importlib.import_module("sessions_history")
    status_tool: Any = importlib.import_module("session_status")
    history_meta = ToolFunction.from_callable(history_tool.sessions_history)
    status_meta = ToolFunction.from_callable(status_tool.session_status)
    assert history_meta.name == "sessions_history"
    assert status_meta.name == "session_status"
