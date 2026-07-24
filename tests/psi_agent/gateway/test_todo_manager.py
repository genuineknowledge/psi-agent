from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._todo_manager import TodoManager


@pytest.mark.anyio
async def test_todos_missing_file_returns_empty(tmp_path: str) -> None:
    todom = TodoManager()
    result = await todom.get(str(tmp_path), "nope")
    assert result["todos"] == []
    assert result["summary"]["total"] == 0


@pytest.mark.anyio
async def test_todos_reads_valid_items_and_skips_bad(tmp_path: str) -> None:
    todom = TodoManager()
    todo_dir = anyio.Path(str(tmp_path)) / ".psi" / "todos"
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

    result = await todom.get(str(tmp_path), "s1")
    assert [t["id"] for t in result["todos"]] == ["1", "2", "3"]
    assert result["summary"] == {
        "total": 3,
        "pending": 1,
        "in_progress": 1,
        "completed": 1,
        "cancelled": 0,
    }


@pytest.mark.anyio
async def test_todos_malformed_json_returns_empty(tmp_path: str) -> None:
    todom = TodoManager()
    todo_dir = anyio.Path(str(tmp_path)) / ".psi" / "todos"
    await todo_dir.mkdir(parents=True)
    await (todo_dir / "bad.json").write_text("{not json", encoding="utf-8")
    result = await todom.get(str(tmp_path), "bad")
    assert result["todos"] == []
