"""Tests for WorkspaceManager browse/roots."""

from __future__ import annotations

import pytest

from psi_agent.gateway._workspace_manager import WorkspaceManager


@pytest.mark.anyio
async def test_browse_returns_segments_and_directories(tmp_path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")

    wm = WorkspaceManager()
    result = await wm.browse(str(tmp_path))

    assert result["path"].replace("\\", "/") == str(tmp_path).replace("\\", "/")
    assert isinstance(result["segments"], list) and len(result["segments"]) >= 1
    names = [e["name"] for e in result["entries"]]
    assert "child" in names
    assert "readme.txt" not in names


@pytest.mark.anyio
async def test_browse_file_kind_includes_files(tmp_path) -> None:
    (tmp_path / "note.md").write_text("# x", encoding="utf-8")

    wm = WorkspaceManager()
    result = await wm.browse(str(tmp_path), kind="file")

    kinds = {e["name"]: e["kind"] for e in result["entries"]}
    assert kinds.get("note.md") == "file"


@pytest.mark.anyio
async def test_list_places_includes_cwd() -> None:
    wm = WorkspaceManager()
    data = await wm.list_places()
    assert isinstance(data["places"], list)
    assert any(r["id"] == "cwd" for r in data["places"])
    assert isinstance(data["drives"], list)
