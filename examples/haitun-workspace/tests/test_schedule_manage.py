"""Tests for the Haitun workspace ``schedule_manage`` tool."""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    assert set(props) == {
        "action",
        "schedule_name",
        "cron",
        "description",
        "content",
        "once_at",
        "visibility",
        "fire",
        "tool",
        "tool_args",
    }
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
    assert "recurring" in msg

    raw = _read(workspace, "daily-report")
    assert 'cron: "0 9 * * *"' in raw
    assert "name: daily-report" in raw
    assert "created_by: agent" in raw
    assert "run_once: false" in raw
    assert "visibility: display" in raw
    assert "Compile and send the report." in raw

    view = await tool.schedule_manage(action="view", schedule_name="daily-report")
    assert "Send the daily report" in view

    listing = await tool.schedule_manage(action="list")
    assert "daily-report [0 9 * * *] [agent]: Send the daily report" in listing


async def test_create_one_shot_once_at(workspace: Path) -> None:
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="remind-meet",
        once_at="2099-07-24 15:30",
        description="Meeting reminder",
        fire="tool",
        tool="feishu_message_send",
        tool_args=('{"receive_id":"oc_testdemo123","text":"Meeting in 5 minutes","receive_id_type":"chat_id"}'),
        visibility="silent",
    )
    assert "one-shot" in msg
    assert "fire='tool'" in msg
    raw = _read(workspace, "remind-meet")
    assert "run_once: true" in raw
    assert "fire: tool" in raw
    assert "feishu_message_send" in raw
    assert "oc_testdemo123" in raw

    listing = await tool.schedule_manage(action="list")
    assert "once" in listing


async def test_create_one_shot_rejects_prose_without_feishu_send(workspace: Path) -> None:
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="bad-prose",
        once_at="2099-07-24 15:30",
        content="# after-work reminder\nsend a message reminding the user to clock out",
        visibility="display",
    )
    assert msg.startswith("[Error]")
    assert "fire='tool'" in msg
    assert not (workspace / "schedules" / "bad-prose" / "TASK.md").exists()


async def test_create_one_shot_rejects_prompt_with_feishu_in_content(workspace: Path) -> None:
    """Legacy path: content embeds feishu_message_send + default fire=prompt — must fail."""
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="bad-legacy",
        once_at="2099-07-24 15:30",
        content=('feishu_message_send(receive_id="oc_testdemo123", text="hi", receive_id_type="chat_id")'),
        visibility="display",
    )
    assert msg.startswith("[Error]")
    assert "fire='tool'" in msg
    assert not (workspace / "schedules" / "bad-legacy" / "TASK.md").exists()


async def test_create_one_shot_rejects_placeholder_receive_id(workspace: Path) -> None:
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="bad-placeholder",
        once_at="2099-07-24 15:30",
        fire="tool",
        tool="feishu_message_send",
        tool_args='{"receive_id":"oc_xxx","text":"hi","receive_id_type":"chat_id"}',
        visibility="silent",
    )
    assert msg.startswith("[Error]")
    assert "placeholder" in msg.casefold()


async def test_create_rejects_both_cron_and_once_at(workspace: Path) -> None:
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="x",
        cron="0 9 * * *",
        once_at="2026-12-01 10:00",
        content="c",
    )
    assert msg.startswith("[Error]")
    assert "both" in msg.lower() or "either" in msg.lower()


async def test_create_rejects_past_once_at(workspace: Path) -> None:
    msg = await tool.schedule_manage(
        action="create",
        schedule_name="past",
        once_at="2020-01-01 10:00",
        content="c",
    )
    assert msg.startswith("[Error]")
    assert "future" in msg.lower()


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


# -- once_at must be judged on the same clock Session fires cron on ------------
#
# The 214 deployment is a UTC base image with ``TZ=Asia/Shanghai``. Session
# resolves the next fire with ``datetime.now(ZoneInfo(TZ))``
# (``ScheduleRegistry._seconds_until_next``), so ``once_at``'s "is this in the
# future?" check has to read the same clock. It used to read the bare machine
# clock, which in that container trails Beijing by 8 hours — long enough for a
# moment already past to be accepted and then scheduled for next year.


def test_now_local_follows_tz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_now_local tracks TZ, not the machine zone."""
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    shanghai = tool._now_local()
    monkeypatch.setenv("TZ", "America/New_York")
    new_york = tool._now_local()

    expected = (
        datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        - datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    ).total_seconds()
    assert abs((shanghai - new_york).total_seconds() - expected) < 5


def test_now_local_falls_back_to_machine_clock_on_bad_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolvable TZ must not crash the tool; behave as before."""
    monkeypatch.setenv("TZ", "Not/AZone")
    assert abs((tool._now_local() - datetime.now()).total_seconds()) < 5


def test_once_at_already_past_in_schedule_zone_is_rejected(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression: past-in-Beijing but future-in-UTC must not be accepted.

    Pinned by construction rather than by wall clock — the offending instant is
    derived from the schedule zone's own "now", so this holds whatever zone the
    machine running the tests happens to be in.
    """
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    past_in_beijing = tool._now_local() - timedelta(hours=2)

    dt, err = tool._parse_once_at(past_in_beijing.strftime("%Y-%m-%d %H:%M"))

    assert dt is None
    assert err is not None
    assert "not in the future" in err
    # The message has to say which clock it judged on, or the caller cannot tell
    # a wrong date from a wrong timezone.
    assert "Asia/Shanghai" in err


def test_once_at_future_in_schedule_zone_is_accepted(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    soon = tool._now_local() + timedelta(hours=2)

    dt, err = tool._parse_once_at(soon.strftime("%Y-%m-%d %H:%M"))

    assert err is None
    assert dt is not None
    assert dt.strftime("%Y-%m-%d %H:%M") == soon.strftime("%Y-%m-%d %H:%M")


def test_iso_offset_once_at_converts_into_schedule_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit-offset input lands on schedule-zone wall time, not machine time."""
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    dt, err = tool._parse_once_at("2099-07-24T15:30:00+00:00")

    assert err is None
    assert dt is not None
    # 15:30 UTC is 23:30 Beijing on the same day.
    assert dt.strftime("%Y-%m-%d %H:%M") == "2099-07-24 23:30"


async def test_created_one_shot_fires_at_the_requested_instant(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: what the tool writes is what Session will fire, to the minute.

    This is the check that would have caught the year-long slip — it runs the
    tool's own cron output back through the registry's countdown instead of
    trusting that both sides read the clock the same way.
    """
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    target = (tool._now_local() + timedelta(hours=3)).replace(second=0, microsecond=0)

    msg = await tool.schedule_manage(
        action="create",
        schedule_name="one-shot-tz",
        once_at=target.strftime("%Y-%m-%d %H:%M"),
        fire="tool",
        tool="feishu_message_send",
        tool_args='{"receive_id":"oc_real_chat","text":"到点啦"}',
    )
    assert not msg.startswith("[Error]"), msg

    registry = await ScheduleRegistry.load(workspace / "schedules")
    schedule = next(s for s in registry.schedules if s.name == "one-shot-tz")
    seconds = ScheduleRegistry._seconds_until_next(schedule.cron)

    expected = (target - tool._now_local()).total_seconds()
    assert abs(seconds - expected) < 90, f"fires in {seconds}s, wanted ~{expected}s"


async def test_create_echoes_resolved_instant_with_weekday(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The confirmation states the weekday, so a bad date derivation is visible."""
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    msg = await tool.schedule_manage(
        action="create",
        schedule_name="echo-instant",
        once_at="2099-07-24 15:30",
        fire="tool",
        tool="feishu_message_send",
        tool_args='{"receive_id":"oc_real_chat","text":"hi"}',
    )

    assert "fires at 2099-07-24 15:30" in msg
    # 2099-07-24 is a Friday.
    assert "Friday" in msg
    assert "周五" in msg
