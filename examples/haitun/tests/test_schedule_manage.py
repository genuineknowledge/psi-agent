"""Tests for the Haitun workspace ``schedule_manage`` tool."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.schedule_registry import ScheduleRegistry
from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tool: Any = importlib.import_module("schedule_manage")


@pytest.fixture()
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the tool at an isolated temporary workspace."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def _read(tmp_path: Path, name: str) -> str:
    return (tmp_path / "schedules" / name / "TASK.md").read_text(encoding="utf-8")


def test_tool_metadata_is_loadable() -> None:
    """The public tool must expose valid metadata for the ToolRegistry."""
    meta = ToolFunction.from_callable(tool.schedule_manage)
    assert meta.name == "schedule_manage"
    props = meta.parameters["properties"]
    assert set(props) == {"action", "schedule_name", "cron", "description", "content"}
    # All params have defaults, so nothing is required.
    assert meta.parameters.get("required", []) == []


async def test_list_empty(workspace: Path) -> None:
    assert await tool.schedule_manage(action="list") == "No schedules found."


async def test_create_view_and_list(workspace: Path) -> None:
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="daily-report",
        cron="0 9 * * *",
        description="Send the daily report",
        content="Compile and send the report.",
    )
    assert "created" in msg

    raw = _read(workspace, "daily-report")
    assert 'cron: "0 9 * * *"' in raw
    assert "name: daily-report" in raw
    assert "created_by: agent" in raw
    assert "Compile and send the report." in raw

    view = await tool.schedule_manage(action="view", schedule_name="daily-report")
    assert "Send the daily report" in view

    listing = await tool.schedule_manage(action="list")
    assert "daily-report [0 9 * * *] [agent]: Send the daily report" in listing


async def test_create_rejects_invalid_cron(workspace: Path) -> None:
    msg = await tool.schedule_manage(action="create", schedule_name="bad", cron="not a cron", content="x")
    assert msg.startswith("[Error]")
    assert not (workspace / "schedules" / "bad").exists()


async def test_create_rejects_bad_name(workspace: Path) -> None:
    msg = await tool.schedule_manage(action="create", schedule_name="../escape", cron="* * * * *", content="x")
    assert msg.startswith("[Error]")


async def test_create_duplicate_is_rejected(workspace: Path) -> None:
    await tool.schedule_manage(action="create", schedule_name="dup", cron="* * * * *", content="a")
    msg = await tool.schedule_manage(action="create", schedule_name="dup", cron="* * * * *", content="b")
    assert msg.startswith("[Error]")
    assert "already exists" in msg


async def test_patch_updates_cron_and_keeps_body(workspace: Path) -> None:
    await tool.schedule_manage(
        action="create",
        schedule_name="job",
        cron="0 9 * * *",
        description="orig",
        content="original body",
    )
    msg = await tool.schedule_manage(action="patch", schedule_name="job", cron="*/15 * * * *")
    assert "patched" in msg

    raw = _read(workspace, "job")
    assert 'cron: "*/15 * * * *"' in raw
    assert "original body" in raw  # body preserved when content omitted
    assert "orig" in raw  # description preserved
    assert "updated_at:" in raw


async def test_patch_rejects_invalid_cron(workspace: Path) -> None:
    await tool.schedule_manage(action="create", schedule_name="job", cron="0 9 * * *", content="b")
    msg = await tool.schedule_manage(action="patch", schedule_name="job", cron="bogus")
    assert msg.startswith("[Error]")
    # Original cron untouched.
    assert 'cron: "0 9 * * *"' in _read(workspace, "job")


async def test_patch_missing_is_error(workspace: Path) -> None:
    msg = await tool.schedule_manage(action="patch", schedule_name="ghost", cron="* * * * *")
    assert msg.startswith("[Error]")
    assert "not found" in msg


async def test_delete_removes_task(workspace: Path) -> None:
    await tool.schedule_manage(action="create", schedule_name="temp", cron="* * * * *", content="b")
    msg = await tool.schedule_manage(action="delete", schedule_name="temp")
    assert "deleted" in msg
    assert not (workspace / "schedules" / "temp").exists()


async def test_delete_missing_is_error(workspace: Path) -> None:
    msg = await tool.schedule_manage(action="delete", schedule_name="ghost")
    assert msg.startswith("[Error]")


async def test_unknown_action(workspace: Path) -> None:
    assert (await tool.schedule_manage(action="frobnicate")).startswith("[Error]")


async def test_created_task_is_loadable_by_registry(workspace: Path) -> None:
    """A task created by the tool must parse cleanly in the schedule registry."""
    await tool.schedule_manage(
        action="create",
        schedule_name="loadable",
        cron="*/30 * * * *",
        description="d",
        content="do the thing",
    )
    registry = await ScheduleRegistry.load(workspace / "schedules")
    names = {s.name for s in registry.schedules}
    assert "loadable" in names
