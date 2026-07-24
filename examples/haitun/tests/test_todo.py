"""Tests for the Haitun workspace ``todo`` tool."""

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

todo_tool: Any = importlib.import_module("todo")
todo_store: Any = importlib.import_module("_todo_store")


def test_tool_metadata_is_loadable() -> None:
    meta = ToolFunction.from_callable(todo_tool.todo)
    assert meta.name == "todo"
    props = meta.parameters["properties"]
    assert set(props) >= {"todos", "merge", "workspace"}


@pytest.mark.anyio
async def test_read_empty_list(tmp_path: Path, app_data_root: Path) -> None:
    assert app_data_root.exists()
    result = await todo_store.read_todos(
        workspace_raw=str(tmp_path),
        session_id="sess-a",
    )
    assert result["ok"] is True
    assert result["todos"] == []
    assert result["summary"]["total"] == 0


@pytest.mark.anyio
async def test_replace_and_read_persists(tmp_path: Path, app_data_root: Path) -> None:
    write = await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "first", "status": "in_progress"},
            {"id": "2", "content": "second", "status": "pending"},
        ],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-a",
    )
    assert write["ok"] is True
    assert write["summary"]["in_progress"] == 1

    path = todo_store.todo_path("sess-a")
    assert await path.exists()

    read = await todo_store.read_todos(workspace_raw=str(tmp_path), session_id="sess-a")
    assert read["todos"][0]["content"] == "first"
    assert read["todos"][1]["status"] == "pending"


@pytest.mark.anyio
async def test_merge_updates_by_id(tmp_path: Path, app_data_root: Path) -> None:
    await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "first", "status": "in_progress"},
            {"id": "2", "content": "second", "status": "pending"},
        ],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-b",
    )
    merged = await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "first", "status": "completed"},
            {"id": "2", "content": "second", "status": "in_progress"},
        ],
        merge=True,
        workspace_raw=str(tmp_path),
        session_id="sess-b",
    )
    assert merged["ok"] is True
    assert merged["summary"]["completed"] == 1
    assert merged["summary"]["in_progress"] == 1
    assert merged["todos"][0]["status"] == "completed"


@pytest.mark.anyio
async def test_merge_appends_new_items(tmp_path: Path, app_data_root: Path) -> None:
    await todo_store.write_todos(
        todos=[{"id": "1", "content": "only", "status": "in_progress"}],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-c",
    )
    merged = await todo_store.write_todos(
        todos=[{"id": "2", "content": "new step", "status": "pending"}],
        merge=True,
        workspace_raw=str(tmp_path),
        session_id="sess-c",
    )
    assert [item["id"] for item in merged["todos"]] == ["1", "2"]


@pytest.mark.anyio
async def test_enforces_single_in_progress(tmp_path: Path, app_data_root: Path) -> None:
    result = await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "a", "status": "in_progress"},
            {"id": "2", "content": "b", "status": "in_progress"},
        ],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-d",
    )
    statuses = [item["status"] for item in result["todos"]]
    assert statuses.count("in_progress") == 1
    assert statuses[-1] == "in_progress"
    assert statuses[0] == "pending"


@pytest.mark.anyio
async def test_invalid_status_rejected(tmp_path: Path, app_data_root: Path) -> None:
    result = await todo_store.write_todos(
        todos=[{"id": "1", "content": "x", "status": "running"}],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-e",
    )
    assert result["ok"] is False
    assert "status" in result["message"]


@pytest.mark.anyio
async def test_todo_tool_read_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(todo_store, "resolve_session_id", lambda: "default")

    await todo_store.write_todos(
        todos=[{"id": "1", "content": "do thing", "status": "pending"}],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="default",
    )
    raw = await todo_tool.todo(workspace=str(tmp_path))
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["session_id"] == "default"
    assert payload["todos"][0]["content"] == "do thing"


@pytest.mark.anyio
async def test_todo_tool_write_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(todo_store, "resolve_session_id", lambda: "sess-tool")

    raw = await todo_tool.todo(
        todos=json.dumps([{"id": "1", "content": "plan", "status": "in_progress"}]),
        workspace=str(tmp_path),
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["session_id"] == "sess-tool"
    assert payload["summary"]["in_progress"] == 1
