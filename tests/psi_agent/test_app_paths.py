"""Unit tests for AppData path helpers and history meta."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from psi_agent._app_paths import app_data_root, history_dir, history_meta_path, state_dir
from psi_agent._history_meta import remove_history_meta, upsert_history_meta


@pytest.mark.anyio
async def test_app_data_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_APP_DATA_ROOT", raising=False)
    root = app_data_root(override=str(tmp_path / "haitun-data"))
    assert root == (tmp_path / "haitun-data").resolve()
    assert state_dir(override=str(tmp_path / "haitun-data")) == root / "state"
    assert history_dir(override=str(tmp_path / "haitun-data")) == root / "history"


@pytest.mark.anyio
async def test_history_meta_upsert_and_remove(tmp_path: Path) -> None:
    override = str(tmp_path / "data")
    await upsert_history_meta(
        session_id="sess1",
        workspace="D:/proj",
        agent="D:/examples/haitun",
        name="demo",
        app_data_override=override,
    )
    meta = history_meta_path(override=override)
    assert meta.is_file()
    lines = meta.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row == {
        "id": "sess1",
        "name": "demo",
        "workspace": "D:/proj",
        "agent": "D:/examples/haitun",
    }

    await upsert_history_meta(
        session_id="sess1",
        workspace="D:/proj2",
        agent="D:/examples/haitun",
        name="demo2",
        app_data_override=override,
    )
    lines = meta.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["workspace"] == "D:/proj2"
    assert json.loads(lines[0])["name"] == "demo2"

    await remove_history_meta(session_id="sess1", app_data_override=override)
    assert meta.read_text(encoding="utf-8").strip() == ""
