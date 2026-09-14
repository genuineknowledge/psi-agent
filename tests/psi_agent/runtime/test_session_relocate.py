"""Unit tests for AppData artifact copy used by POST /sessions/{id}/relocate."""

from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent._appdata import (
    appdata_history_path,
    appdata_todo_path,
    appdata_todo_segments_path,
)
from psi_agent.runtime._session_relocate import (
    copy_session_artifacts,
    delete_session_todo_files,
)


@pytest.mark.anyio
async def test_copy_session_artifacts_prefers_appdata(tmp_path: Path) -> None:
    appdata = str(tmp_path / "appdata")
    old_id = "sess-old"
    new_id = "sess-new"
    hist = appdata_history_path(appdata, old_id)
    await hist.parent.mkdir(parents=True, exist_ok=True)
    await hist.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
    todo = appdata_todo_path(appdata, old_id)
    await todo.parent.mkdir(parents=True, exist_ok=True)
    await todo.write_text(
        '{"todos":[{"id":"1","content":"a","status":"pending"}]}',
        encoding="utf-8",
    )
    segs = appdata_todo_segments_path(appdata, old_id)
    await segs.write_text('{"segments":[]}', encoding="utf-8")

    await copy_session_artifacts(
        old_session_id=old_id,
        new_session_id=new_id,
        old_workspace=str(tmp_path / "ws"),
        appdata=appdata,
    )

    new_hist = appdata_history_path(appdata, new_id)
    new_todo = appdata_todo_path(appdata, new_id)
    new_segs = appdata_todo_segments_path(appdata, new_id)
    assert await new_hist.read_text(encoding="utf-8") == await hist.read_text(encoding="utf-8")
    assert await new_todo.read_text(encoding="utf-8") == await todo.read_text(encoding="utf-8")
    assert await new_segs.read_text(encoding="utf-8") == await segs.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_delete_session_todo_files_is_best_effort(tmp_path: Path) -> None:
    appdata = str(tmp_path / "appdata")
    sid = "sess-gone"
    todo = appdata_todo_path(appdata, sid)
    await todo.parent.mkdir(parents=True, exist_ok=True)
    await todo.write_text("{}", encoding="utf-8")
    await delete_session_todo_files(sid, appdata=appdata)
    assert not await todo.exists()
    # Missing files must not raise.
    await delete_session_todo_files(sid, appdata=appdata)
