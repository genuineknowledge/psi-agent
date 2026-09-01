from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from psi_agent.runtime._todo_manager import TodoManager


@pytest.mark.anyio
async def test_todos_missing_file_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / "appdata"))
    todom = TodoManager()
    result = await todom.get(str(tmp_path / "ws"), "nope")
    assert result["todos"] == []
    assert result["summary"]["total"] == 0


@pytest.mark.anyio
async def test_todos_reads_appdata_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    todom = TodoManager()
    todo_dir = anyio.Path(str(appdata)) / "todos"
    await todo_dir.mkdir(parents=True)
    payload = """{
  "session_id": "s1",
  "todos": [
    {"id": "1", "content": "plan", "status": "completed"},
    {"id": "2", "content": "implement", "status": "in_progress"},
    {"id": "3", "content": "verify", "status": "pending"},
    {"id": "", "content": "bad", "status": "pending"},
    {"id": "4", "content": "x", "status": "running"},
    "not-an-object"
  ]
}
"""
    await (todo_dir / "s1.json").write_text(payload, encoding="utf-8")

    result = await todom.get(str(tmp_path / "ws"), "s1", appdata=str(appdata))
    assert [t["id"] for t in result["todos"]] == ["1", "2", "3"]
    assert result["summary"] == {
        "total": 3,
        "pending": 1,
        "in_progress": 1,
        "completed": 1,
        "cancelled": 0,
    }


@pytest.mark.anyio
async def test_todos_dual_read_falls_back_to_workspace_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    ws = tmp_path / "ws"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    await anyio.Path(appdata).mkdir()
    legacy_dir = anyio.Path(str(ws)) / ".psi" / "todos"
    await legacy_dir.mkdir(parents=True)
    await (legacy_dir / "old.json").write_text(
        '{"todos":[{"id":"1","content":"legacy","status":"pending"}]}',
        encoding="utf-8",
    )
    todom = TodoManager()
    result = await todom.get(str(ws), "old", appdata=str(appdata))
    assert result["todos"] == [{"id": "1", "content": "legacy", "status": "pending"}]


@pytest.mark.anyio
async def test_todos_appdata_wins_over_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    ws = tmp_path / "ws"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    app_dir = anyio.Path(str(appdata)) / "todos"
    legacy_dir = anyio.Path(str(ws)) / ".psi" / "todos"
    await app_dir.mkdir(parents=True)
    await legacy_dir.mkdir(parents=True)
    await (app_dir / "s1.json").write_text(
        '{"todos":[{"id":"a","content":"from-appdata","status":"completed"}]}',
        encoding="utf-8",
    )
    await (legacy_dir / "s1.json").write_text(
        '{"todos":[{"id":"b","content":"from-legacy","status":"pending"}]}',
        encoding="utf-8",
    )
    todom = TodoManager()
    result = await todom.get(str(ws), "s1", appdata=str(appdata))
    assert result["todos"][0]["content"] == "from-appdata"


@pytest.mark.anyio
async def test_todos_malformed_json_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    todom = TodoManager()
    todo_dir = anyio.Path(str(appdata)) / "todos"
    await todo_dir.mkdir(parents=True)
    await (todo_dir / "bad.json").write_text("{not json", encoding="utf-8")
    result = await todom.get(str(tmp_path / "ws"), "bad", appdata=str(appdata))
    assert result["todos"] == []


@pytest.mark.anyio
async def test_todo_segments_list_and_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    todo_dir = anyio.Path(str(appdata)) / "todos"
    await todo_dir.mkdir(parents=True)
    payload = {
        "session_id": "s1",
        "segments": [
            {
                "id": "seg-old",
                "created_at": "2026-07-30T01:00:00+00:00",
                "updated_at": "2026-07-30T01:10:00+00:00",
                "closed_at": "2026-07-30T01:10:00+00:00",
                "label": "旧子任务",
                "source": "todo.replace",
                "todos": [
                    {"id": "1", "content": "a", "status": "completed"},
                    {"id": "2", "content": "b", "status": "completed"},
                ],
            },
            {
                "id": "seg-new",
                "created_at": "2026-07-30T02:00:00+00:00",
                "updated_at": "2026-07-30T02:05:00+00:00",
                "closed_at": None,
                "label": "新子任务",
                "source": "todo.replace",
                "todos": [{"id": "1", "content": "x", "status": "in_progress"}],
            },
        ],
    }
    await (todo_dir / "s1.segments.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    todom = TodoManager()
    listed = await todom.list_segments("s1", appdata=str(appdata))
    assert [s["id"] for s in listed] == ["seg-new", "seg-old"]
    assert listed[0]["summary"]["in_progress"] == 1
    got = await todom.get_segment("s1", "seg-old", appdata=str(appdata))
    assert got is not None
    assert got["todos"][0]["content"] == "a"
    patched = await todom.set_segment_label("s1", "seg-new", "  侧栏进度改造  ", appdata=str(appdata))
    assert patched is not None
    assert patched["label"] == "侧栏进度改造"
