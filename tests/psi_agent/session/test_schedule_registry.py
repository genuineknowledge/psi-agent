from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from croniter import croniter

from psi_agent._yaml import parse_yaml_header
from psi_agent.session.schedule_registry import Schedule, ScheduleEntry, ScheduleRegistry

# ── helpers ───────────────────────────────────────────────────────────────────


class _MockAgent:
    _lock = anyio.Lock()

    async def run(self, msg: object, **_kwargs: object) -> Any:  # type: ignore[return]
        if False:
            yield

    def set_pending_schedule_chunks(self, chunks: object) -> None:
        pass


class _RaisingAgent:
    _lock = anyio.Lock()

    async def run(self, msg: object, **_kwargs: object) -> Any:  # type: ignore[return]
        if False:
            yield
        raise RuntimeError("test error")

    def set_pending_schedule_chunks(self, chunks: object) -> None:
        pass


# ── Schedule dataclass ────────────────────────────────────────────────────────


def test_schedule_dataclass_fields() -> None:
    s = Schedule(name="test", cron="* * * * *", task_content="Run")
    assert s.name == "test"
    assert s.cron == "* * * * *"
    assert s.task_content == "Run"
    assert s.visibility == "display"


# ── ScheduleEntry ─────────────────────────────────────────────────────────────


def test_schedule_entry_defaults() -> None:
    s = Schedule(name="t", cron="* * * * *", task_content="x")
    entry = ScheduleEntry(file_hash="abc", schedule=s)
    assert entry.file_hash == "abc"
    assert entry.schedule is s
    assert entry.fresh is False


def test_schedule_entry_fresh_flag() -> None:
    s = Schedule(name="t", cron="* * * * *", task_content="x")
    entry = ScheduleEntry(file_hash="abc", schedule=s, fresh=True)
    assert entry.fresh is True


# ── _load_from_dir ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_load_schedule_with_yaml_header(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "daily-report"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text(
        textwrap.dedent("""\
        ---
        name: daily-report
        cron: "0 12 * * *"
        ---
        请生成项目进展日报。
    """),
        encoding="utf-8",
    )

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(files) == 1
    entry = next(iter(files.values()))
    assert entry.fresh is True
    assert entry.schedule.name == "daily-report"
    assert entry.schedule.cron == "0 12 * * *"
    assert entry.schedule.visibility == "display"
    assert "请生成项目进展日报" in entry.schedule.task_content


@pytest.mark.anyio
async def test_load_schedule_visibility_silent(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "heartbeat"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text(
        textwrap.dedent("""\
        ---
        name: heartbeat
        cron: "*/30 * * * *"
        visibility: silent
        ---
        Respond with HEARTBEAT_OK
    """),
        encoding="utf-8",
    )

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    entry = next(iter(files.values()))
    assert entry.schedule.visibility == "silent"


@pytest.mark.anyio
async def test_load_schedule_missing_yaml_header(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "no-header"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text("Just a task without header.", encoding="utf-8")

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(files) == 0


@pytest.mark.anyio
async def test_load_multiple_schedules(tmp_path: Path) -> None:
    for name in ["daily", "weekly"]:
        d = tmp_path / "schedules" / name
        await anyio.Path(d).mkdir(parents=True)
        await anyio.Path(d / "TASK.md").write_text(
            f'---\nname: {name}\ncron: "0 12 * * *"\n---\nTask: {name}', encoding="utf-8"
        )

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(files) == 2
    names = {entry.schedule.name for entry in files.values()}
    assert names == {"daily", "weekly"}


@pytest.mark.anyio
async def test_load_schedules_missing_dir(tmp_path: Path) -> None:
    files = await ScheduleRegistry._load_from_dir(tmp_path / "nonexistent")
    assert len(files) == 0


@pytest.mark.anyio
async def test_load_schedule_missing_name(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "bad"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text('---\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8")

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(files) == 0


@pytest.mark.anyio
async def test_load_schedule_invalid_cron_skipped(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "bad"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text(
        '---\nname: bad\ncron: "not a cron"\n---\nTask', encoding="utf-8"
    )

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(files) == 0


@pytest.mark.anyio
async def test_load_from_dir_skip_unchanged(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "daily"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    old_files = files

    result = await ScheduleRegistry._load_from_dir(tmp_path / "schedules", old_files)
    assert len(result) == 1
    entry = next(iter(result.values()))
    assert entry.fresh is False
    assert entry.schedule.name == "daily"


@pytest.mark.anyio
async def test_load_from_dir_imports_changed(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "daily"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )

    files = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    old_files = files

    await anyio.Path(schedules_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 6 * * *"\n---\nUpdated task', encoding="utf-8"
    )

    result = await ScheduleRegistry._load_from_dir(tmp_path / "schedules", old_files)
    entry = next(iter(result.values()))
    assert entry.fresh is True
    assert entry.schedule.cron == "0 6 * * *"


# ── ScheduleRegistry factory ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_registry_load(tmp_path: Path) -> None:
    sched_dir = tmp_path / "schedules" / "daily"
    await anyio.Path(sched_dir).mkdir(parents=True)
    await anyio.Path(sched_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )

    sr = await ScheduleRegistry.load(tmp_path / "schedules")
    assert len(sr.schedules) == 1
    assert sr.schedules[0].name == "daily"
    assert sr._work_dir == tmp_path / "schedules"


@pytest.mark.anyio
async def test_registry_load_missing_dir(tmp_path: Path) -> None:
    sr = await ScheduleRegistry.load(tmp_path / "nonexistent")
    assert sr.schedules == []
    assert sr._work_dir == tmp_path / "nonexistent"


# ── ScheduleRegistry.refresh ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_refresh_no_work_dir() -> None:
    sr = ScheduleRegistry()
    assert await sr.refresh() == {}


@pytest.mark.anyio
async def test_refresh_no_task_group() -> None:
    sched_dir = Path("/tmp")
    sr = ScheduleRegistry(work_dir=sched_dir)
    assert await sr.refresh() == {}


@pytest.mark.anyio
async def test_refresh_adds_new_schedule(tmp_path: Path) -> None:
    sr = await ScheduleRegistry.load(tmp_path / "nonexistent")
    sched_dir = tmp_path / "schedules" / "extra"
    await anyio.Path(sched_dir).mkdir(parents=True)
    await anyio.Path(sched_dir / "TASK.md").write_text(
        '---\nname: extra\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )
    sr._work_dir = tmp_path / "schedules"

    agent = _MockAgent()
    sr._agent = cast(Any, agent)
    async with anyio.create_task_group() as tg:
        sr._task_group = tg
        added = await sr.refresh()
        assert added == {"extra": "added"}
        tg.cancel_scope.cancel()
    assert len(sr.schedules) == 1
    assert sr.schedules[0].name == "extra"


@pytest.mark.anyio
async def test_refresh_skips_existing(tmp_path: Path) -> None:
    sched_dir = tmp_path / "schedules" / "daily"
    await anyio.Path(sched_dir).mkdir(parents=True)
    await anyio.Path(sched_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )

    sr = await ScheduleRegistry.load(tmp_path / "schedules")
    sr._agent = cast(Any, _MockAgent())
    async with anyio.create_task_group() as tg:
        sr._task_group = tg
        result = await sr.refresh()
        assert result == {"daily": "skipped"}
        tg.cancel_scope.cancel()
    assert len(sr.schedules) == 1


@pytest.mark.anyio
async def test_refresh_updates_modified_schedule(tmp_path: Path) -> None:
    sched_dir = tmp_path / "schedules" / "daily"
    await anyio.Path(sched_dir).mkdir(parents=True)
    await anyio.Path(sched_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )

    sr = await ScheduleRegistry.load(tmp_path / "schedules")
    sr._agent = cast(Any, _MockAgent())
    async with anyio.create_task_group() as tg:
        sr._task_group = tg
        # initial refresh: skip (unchanged)
        result = await sr.refresh()
        assert result == {"daily": "skipped"}

        # modify
        await anyio.Path(sched_dir / "TASK.md").write_text(
            '---\nname: daily\ncron: "0 6 * * *"\n---\nUpdated', encoding="utf-8"
        )

        result = await sr.refresh()
        assert result == {"daily": "updated"}
        assert sr.schedules[0].cron == "0 6 * * *"
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_refresh_removes_deleted_schedule(tmp_path: Path) -> None:
    sched_dir = tmp_path / "schedules" / "daily"
    await anyio.Path(sched_dir).mkdir(parents=True)
    await anyio.Path(sched_dir / "TASK.md").write_text(
        '---\nname: daily\ncron: "0 12 * * *"\n---\nTask', encoding="utf-8"
    )

    sr = await ScheduleRegistry.load(tmp_path / "schedules")
    sr._agent = cast(Any, _MockAgent())
    async with anyio.create_task_group() as tg:
        sr._task_group = tg
        # delete the schedule dir
        await anyio.Path(sched_dir / "TASK.md").unlink()
        await anyio.Path(sched_dir).rmdir()

        result = await sr.refresh()
        assert result == {"daily": "removed"}
        assert sr.schedules == []
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_refresh_mixed_changes(tmp_path: Path) -> None:
    """Add, modify, delete, and skip all in one refresh."""
    sched_dir = tmp_path / "schedules"
    await anyio.Path(sched_dir).mkdir()

    for name in ["keep", "modify", "delete"]:
        d = sched_dir / name
        await anyio.Path(d).mkdir()
        await anyio.Path(d / "TASK.md").write_text(
            f'---\nname: {name}\ncron: "0 12 * * *"\n---\nTask: {name}', encoding="utf-8"
        )

    sr = await ScheduleRegistry.load(tmp_path / "schedules")
    sr._agent = cast(Any, _MockAgent())
    async with anyio.create_task_group() as tg:
        sr._task_group = tg

        # modify
        await anyio.Path(sched_dir / "modify" / "TASK.md").write_text(
            '---\nname: modify\ncron: "0 6 * * *"\n---\nChanged', encoding="utf-8"
        )
        # delete
        await anyio.Path(sched_dir / "delete" / "TASK.md").unlink()
        await anyio.Path(sched_dir / "delete").rmdir()
        # add
        d = sched_dir / "newone"
        await anyio.Path(d).mkdir()
        await anyio.Path(d / "TASK.md").write_text(
            '---\nname: newone\ncron: "0 12 * * *"\n---\nFresh', encoding="utf-8"
        )

        result = await sr.refresh()
        assert result == {"keep": "skipped", "modify": "updated", "delete": "removed", "newone": "added"}
        names = {s.name for s in sr.schedules}
        assert names == {"keep", "modify", "newone"}
        tg.cancel_scope.cancel()


# ── _run_one error handling ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_one_handles_agent_error() -> None:
    s = Schedule(name="test", cron="* * * * * *", task_content="ping")
    agent = _RaisingAgent()
    cancel_scope = anyio.CancelScope()
    with anyio.move_on_after(3):
        await ScheduleRegistry._run_one(s, cast(Any, agent), cancel_scope)


# ── timezone-aware scheduling ─────────────────────────────────────────────────


def test_schedule_tz_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ", raising=False)
    assert ScheduleRegistry._schedule_tz() is None


def test_schedule_tz_invalid_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Not/AZone")
    assert ScheduleRegistry._schedule_tz() is None


def test_schedule_tz_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    tz = ScheduleRegistry._schedule_tz()
    assert tz is not None
    assert str(tz) == "Asia/Shanghai"


def test_cron_anchored_to_local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    """'0 9 * * *' must fire at 09:00 *local* time, not 09:00 UTC.

    Regression guard for the missed-schedule bug: with the tz applied,
    the next fire converted back to Asia/Shanghai reads 09:00; the old
    bare-epoch base would land it at 17:00 Shanghai (09:00 UTC).
    """
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    tz = ScheduleRegistry._schedule_tz()
    assert tz is not None

    it = croniter("0 9 * * *", datetime.now(tz))
    next_local = datetime.fromtimestamp(it.get_next(float), tz)
    assert next_local.hour == 9
    assert next_local.minute == 0


# ── YAML parse helper ─────────────────────────────────────────────────────────


def test_parse_yaml_header_error() -> None:
    content = "---\n: invalid yaml: :\n---\nbody"
    header, body = parse_yaml_header(content)
    assert header is None
    assert body == content


def test_parse_yaml_header_success() -> None:
    content = "---\nname: daily-report\ncron: '0 12 * * *'\n---\n请生成日报。"
    header, body = parse_yaml_header(content)
    assert header == {"name": "daily-report", "cron": "0 12 * * *"}
    assert body == "请生成日报。"
