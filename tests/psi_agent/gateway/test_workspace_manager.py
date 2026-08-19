"""Tests for WorkspaceManager browse/roots."""

from __future__ import annotations

import anyio
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
async def test_browse_handles_os_error_entry(tmp_path, monkeypatch) -> None:
    (tmp_path / "good.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "bad.txt").write_text("err", encoding="utf-8")

    # Mock anyio.Path.resolve to raise PermissionError for bad.txt
    original_resolve = anyio.Path.resolve

    async def fake_resolve(self: anyio.Path) -> anyio.Path:
        if self.name == "bad.txt":
            raise PermissionError("Access denied")
        return await original_resolve(self)

    monkeypatch.setattr(anyio.Path, "resolve", fake_resolve)

    wm = WorkspaceManager()
    result = await wm.browse(str(tmp_path), kind="all")

    names = [e["name"] for e in result["entries"]]
    assert "good.txt" in names
    assert "bad.txt" not in names


@pytest.mark.anyio
async def test_list_places_includes_cwd() -> None:
    wm = WorkspaceManager()
    data = await wm.list_places()
    assert isinstance(data["places"], list)
    assert any(r["id"] == "cwd" for r in data["places"])
    assert isinstance(data["drives"], list)


@pytest.mark.anyio
async def test_reveal_requires_path() -> None:
    wm = WorkspaceManager()
    with pytest.raises(ValueError, match="path is required"):
        await wm.reveal("  ")


@pytest.mark.anyio
async def test_reveal_missing_path(tmp_path) -> None:
    wm = WorkspaceManager()
    with pytest.raises(FileNotFoundError):
        await wm.reveal(str(tmp_path / "no-such-file.txt"))


@pytest.mark.anyio
async def test_reveal_invokes_platform_launcher(tmp_path, monkeypatch) -> None:
    target = tmp_path / "out.md"
    target.write_text("hi", encoding="utf-8")
    calls: list[list[str]] = []

    async def fake_run_process(cmd: list[str] | tuple[str, ...], **_kwargs: object) -> None:
        calls.append(list(cmd))

    monkeypatch.setattr("psi_agent.gateway._workspace_manager.anyio.run_process", fake_run_process)
    monkeypatch.setattr("psi_agent.gateway._workspace_manager.sys.platform", "win32")

    wm = WorkspaceManager()
    result = await wm.reveal(str(target))
    assert result["ok"] is True
    assert result["path"].replace("\\", "/").endswith("out.md")
    assert len(calls) == 1
    assert calls[0][0] == "explorer"
    assert calls[0][1].startswith("/select,")
    assert "out.md" in calls[0][1]
