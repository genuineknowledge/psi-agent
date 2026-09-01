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
async def test_read_empty_list(tmp_path: Path) -> None:
    result = await todo_store.read_todos(
        workspace_raw=str(tmp_path),
        session_id="sess-a",
    )
    assert result["ok"] is True
    assert result["todos"] == []
    assert result["summary"]["total"] == 0


@pytest.mark.anyio
async def test_replace_and_read_persists(tmp_path: Path) -> None:
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

    path = todo_store.anyio.Path(write["path"])
    assert await path.exists()

    read = await todo_store.read_todos(workspace_raw=str(tmp_path), session_id="sess-a")
    assert read["todos"][0]["content"] == "first"
    assert read["todos"][1]["status"] == "pending"


@pytest.mark.anyio
async def test_merge_updates_by_id(tmp_path: Path) -> None:
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
async def test_replace_opens_new_segment_and_closes_previous(
    tmp_path: Path,
    _todo_appdata: Path,
) -> None:
    first = await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "读本地文档", "status": "in_progress"},
            {"id": "2", "content": "整理摘要", "status": "pending"},
        ],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-seg",
    )
    assert first["ok"] is True
    assert first["segment_id"]
    await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "读本地文档", "status": "completed"},
            {"id": "2", "content": "整理摘要", "status": "completed"},
        ],
        merge=True,
        workspace_raw=str(tmp_path),
        session_id="sess-seg",
    )
    second = await todo_store.write_todos(
        todos=[
            {"id": "a", "content": "改侧栏进度", "status": "in_progress"},
            {"id": "b", "content": "补测试", "status": "pending"},
        ],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-seg",
    )
    assert second["ok"] is True
    assert second["segment_id"] != first["segment_id"]

    seg_path = todo_store.appdata_todo_segments_path(str(_todo_appdata), "sess-seg")
    assert await seg_path.exists()
    payload = json.loads(await seg_path.read_text(encoding="utf-8"))
    segments = payload["segments"]
    assert len(segments) == 2
    closed, opened = segments[0], segments[1]
    assert closed["closed_at"]
    assert closed["todos"][0]["status"] == "completed"
    assert closed["label"].startswith("读本地文档") or "读本地文档" in closed["label"]
    assert opened["closed_at"] is None
    assert opened["todos"][0]["content"] == "改侧栏进度"
    assert opened["id"] == second["segment_id"]


@pytest.mark.anyio
async def test_merge_keeps_single_open_segment(tmp_path: Path, _todo_appdata: Path) -> None:
    await todo_store.write_todos(
        todos=[{"id": "1", "content": "only", "status": "in_progress"}],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-merge-seg",
    )
    await todo_store.write_todos(
        todos=[{"id": "1", "content": "only", "status": "completed"}],
        merge=True,
        workspace_raw=str(tmp_path),
        session_id="sess-merge-seg",
    )
    seg_path = todo_store.appdata_todo_segments_path(str(_todo_appdata), "sess-merge-seg")
    payload = json.loads(await seg_path.read_text(encoding="utf-8"))
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["closed_at"] is None
    assert payload["segments"][0]["todos"][0]["status"] == "completed"


def test_looks_self_referential() -> None:
    assert todo_store.looks_self_referential("更新清单状态并回复用户")
    assert todo_store.looks_self_referential("Reply to the user with results")
    assert not todo_store.looks_self_referential("确认 segment-test-b.md 已落盘")


@pytest.mark.anyio
async def test_write_self_ref_returns_warnings(tmp_path: Path, _todo_appdata: Path) -> None:
    result = await todo_store.write_todos(
        todos=[
            {"id": "1", "content": "写文件", "status": "completed"},
            {"id": "2", "content": "更新清单状态并回复用户", "status": "in_progress"},
        ],
        merge=False,
        workspace_raw=str(tmp_path),
        session_id="sess-warn",
    )
    assert result["ok"] is True
    assert any("self-referential" in w for w in result["warnings"])
    assert result["todos"][1]["status"] == "in_progress"


@pytest.mark.anyio
async def test_merge_appends_new_items(tmp_path: Path) -> None:
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
async def test_enforces_single_in_progress(tmp_path: Path) -> None:
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
async def test_invalid_status_rejected(tmp_path: Path) -> None:
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


@pytest.mark.anyio
async def test_accepts_native_tool_call_array(tmp_path: Path) -> None:
    todos = [{"id": "1", "content": "审查协议", "status": "in_progress"}]

    raw = await todo_tool.todo(todos=todos, workspace=str(tmp_path))
    result = json.loads(raw)

    assert result["ok"] is True
    assert result["todos"] == todos
