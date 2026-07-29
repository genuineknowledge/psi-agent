"""Tests for WorkspaceManager browsing, places, and reusable workflows."""

from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._workspace_manager import WorkspaceManager


async def _write_workflow(
    workspace: anyio.Path,
    name: str,
) -> anyio.Path:
    workflow_dir = workspace / "flows" / "workflows" / name
    await workflow_dir.mkdir(parents=True, exist_ok=True)
    await (workflow_dir / f"{name}.workflow").write_text(
        f"workflow {name.replace('-', '_')} {{}}",
        encoding="utf-8",
    )
    return workflow_dir


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


@pytest.mark.anyio
async def test_list_workflows_returns_valid_sorted_summaries(tmp_path) -> None:
    workspace = anyio.Path(str(tmp_path))
    await _write_workflow(workspace, "zeta-flow")
    await _write_workflow(workspace, "alpha-flow")

    result = await WorkspaceManager().list_workflows(str(workspace))

    assert result == [
        {
            "name": "alpha-flow",
            "path": "flows/workflows/alpha-flow/alpha-flow.workflow",
        },
        {
            "name": "zeta-flow",
            "path": "flows/workflows/zeta-flow/zeta-flow.workflow",
        },
    ]


@pytest.mark.anyio
async def test_list_workflows_skips_corrupt_and_incomplete_entries(tmp_path) -> None:
    workspace = anyio.Path(str(tmp_path))
    await _write_workflow(workspace, "valid-flow")

    missing_source = workspace / "flows" / "workflows" / "missing-source"
    await missing_source.mkdir(parents=True)
    await (missing_source / "other.workflow").write_text(
        "workflow other {}",
        encoding="utf-8",
    )

    invalid_name = await _write_workflow(workspace, "Invalid_Name")
    assert await invalid_name.exists()

    result = await WorkspaceManager().list_workflows(str(workspace))

    assert [workflow["name"] for workflow in result] == ["valid-flow"]


@pytest.mark.parametrize(
    "name",
    [
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com9",
        "lpt1",
        "lpt9",
    ],
)
def test_workflow_name_rejects_windows_reserved_names(name: str) -> None:
    assert not WorkspaceManager._is_valid_workflow_name(name)


@pytest.mark.anyio
async def test_list_workflows_skips_symlinked_entries_and_sources(tmp_path) -> None:
    workspace = anyio.Path(str(tmp_path))
    registry = workspace / "flows" / "workflows"
    await registry.mkdir(parents=True)

    outside = workspace / "outside"
    await _write_workflow(outside, "linked-flow")
    outside_entry = outside / "flows" / "workflows" / "linked-flow"
    try:
        await (registry / "linked-flow").symlink_to(outside_entry, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    source_link = await _write_workflow(workspace, "source-link")
    await (source_link / "source-link.workflow").unlink()
    await (source_link / "source-link.workflow").symlink_to(outside_entry / "linked-flow.workflow")

    result = await WorkspaceManager().list_workflows(str(workspace))

    assert result == []


@pytest.mark.anyio
async def test_list_workflows_skips_symlinked_registry_components(tmp_path) -> None:
    workspace = anyio.Path(str(tmp_path))
    real_flows = workspace / "real-flows"
    await _write_workflow(real_flows, "linked-flow")
    try:
        await (workspace / "flows").symlink_to(real_flows / "flows", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    assert await WorkspaceManager().list_workflows(str(workspace)) == []

    await (workspace / "flows").unlink()
    await (workspace / "flows").mkdir()
    await (workspace / "flows" / "workflows").symlink_to(real_flows / "flows" / "workflows", target_is_directory=True)

    assert await WorkspaceManager().list_workflows(str(workspace)) == []


@pytest.mark.anyio
async def test_list_workflows_skips_oversized_source(tmp_path) -> None:
    workspace = anyio.Path(str(tmp_path))
    oversized_source = await _write_workflow(workspace, "oversized-source")
    await (oversized_source / "oversized-source.workflow").write_bytes(b"x" * (8 * 1024 * 1024 + 1))

    assert await WorkspaceManager().list_workflows(str(workspace)) == []


@pytest.mark.anyio
async def test_list_workflows_missing_registry_is_empty(tmp_path) -> None:
    result = await WorkspaceManager().list_workflows(str(tmp_path))
    assert result == []


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
