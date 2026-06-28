from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from psi_agent._yaml import parse_yaml_header
from psi_agent.session.schedule_registry import Schedule, ScheduleRegistry


@pytest.mark.anyio
async def test_load_schedule_with_yaml_header(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "daily-report"
    schedules_dir.mkdir(parents=True)
    (schedules_dir / "TASK.md").write_text(
        textwrap.dedent("""\
        ---
        name: daily-report
        cron: "0 12 * * *"
        ---
        请生成项目进展日报。
    """)
    )

    schedules = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(schedules) == 1
    s = schedules[0]
    assert s.name == "daily-report"
    assert s.cron == "0 12 * * *"
    assert "请生成项目进展日报" in s.task_content


@pytest.mark.anyio
async def test_load_schedule_missing_yaml_header(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "no-header"
    schedules_dir.mkdir(parents=True)
    (schedules_dir / "TASK.md").write_text("Just a task without header.")

    schedules = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(schedules) == 0


@pytest.mark.anyio
async def test_load_multiple_schedules(tmp_path: Path) -> None:
    for name in ["daily", "weekly"]:
        d = tmp_path / "schedules" / name
        d.mkdir(parents=True)
        (d / "TASK.md").write_text(f'---\nname: {name}\ncron: "0 12 * * *"\n---\nTask: {name}')

    schedules = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(schedules) == 2
    names = {s.name for s in schedules}
    assert names == {"daily", "weekly"}


@pytest.mark.anyio
async def test_load_schedules_missing_dir(tmp_path: Path) -> None:
    schedules = await ScheduleRegistry._load_from_dir(tmp_path / "nonexistent")
    assert len(schedules) == 0


@pytest.mark.anyio
async def test_load_schedule_missing_name(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "bad"
    schedules_dir.mkdir(parents=True)
    (schedules_dir / "TASK.md").write_text('---\ncron: "0 12 * * *"\n---\nTask')

    schedules = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(schedules) == 0


def test_schedule_dataclass_fields() -> None:
    s = Schedule(name="test", cron="* * * * *", task_content="Run")
    assert s.name == "test"
    assert s.cron == "* * * * *"
    assert s.task_content == "Run"


# --- Missing coverage tests ---


def test_parse_yaml_header_error() -> None:
    # Malformed YAML
    content = "---\n: invalid yaml: :\n---\nbody"
    header, body = parse_yaml_header(content)
    assert header is None
    assert body == content


def test_parse_yaml_header_success() -> None:
    """parse_yaml_header correctly extracts YAML front matter and separates body."""
    content = "---\nname: daily-report\ncron: '0 12 * * *'\n---\n请生成日报。"
    header, body = parse_yaml_header(content)
    assert header == {"name": "daily-report", "cron": "0 12 * * *"}
    assert body == "请生成日报。"


@pytest.mark.anyio
async def test_load_schedule_invalid_cron_skipped(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "bad"
    schedules_dir.mkdir(parents=True)
    (schedules_dir / "TASK.md").write_text('---\nname: bad\ncron: "not a cron"\n---\nTask')

    schedules = await ScheduleRegistry._load_from_dir(tmp_path / "schedules")
    assert len(schedules) == 0


# ── ScheduleRegistry factory ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_registry_load(tmp_path: Path) -> None:
    sched_dir = tmp_path / "schedules" / "daily"
    sched_dir.mkdir(parents=True)
    (sched_dir / "TASK.md").write_text('---\nname: daily\ncron: "0 12 * * *"\n---\nTask')

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


class _MockAgent:
    _lock = anyio.Lock()

    async def run(self, msg):  # type: ignore[return]
        if False:
            yield

    def set_pending_schedule_chunks(self, chunks: object) -> None:
        pass


@pytest.mark.anyio
async def test_refresh_no_work_dir() -> None:
    sr = ScheduleRegistry()
    async with anyio.create_task_group() as tg:
        assert await sr.refresh(tg) == []


@pytest.mark.anyio
async def test_refresh_no_agent() -> None:
    sched_dir = Path("/tmp")
    sr = ScheduleRegistry(work_dir=sched_dir)
    async with anyio.create_task_group() as tg:
        assert await sr.refresh(tg) == []


@pytest.mark.anyio
async def test_refresh_adds_new_schedule(tmp_path: Path) -> None:
    sr = await ScheduleRegistry.load(tmp_path / "nonexistent")
    # Override work_dir to point to a real schedules dir
    sched_dir = tmp_path / "schedules" / "extra"
    sched_dir.mkdir(parents=True)
    (sched_dir / "TASK.md").write_text('---\nname: extra\ncron: "0 12 * * *"\n---\nTask')
    sr._work_dir = tmp_path / "schedules"

    agent = _MockAgent()
    sr._agent = cast(Any, agent)
    async with anyio.create_task_group() as tg:
        added = await sr.refresh(tg)
        assert len(added) == 1
        assert added[0].name == "extra"
        tg.cancel_scope.cancel()
    assert len(sr.schedules) == 1


@pytest.mark.anyio
async def test_refresh_skips_existing(tmp_path: Path) -> None:
    sched_dir = tmp_path / "schedules" / "daily"
    sched_dir.mkdir(parents=True)
    (sched_dir / "TASK.md").write_text('---\nname: daily\ncron: "0 12 * * *"\n---\nTask')

    sr = await ScheduleRegistry.load(tmp_path / "schedules")
    sr._agent = cast(Any, _MockAgent())
    async with anyio.create_task_group() as tg:
        added = await sr.refresh(tg)
        assert added == []
        tg.cancel_scope.cancel()
    assert len(sr.schedules) == 1


# ── _run_one error handling ───────────────────────────────────────────────────


class _RaisingAgent:
    _lock = anyio.Lock()

    async def run(self, msg):  # type: ignore[return]
        if False:
            yield
        raise RuntimeError("test error")

    def set_pending_schedule_chunks(self, chunks: object) -> None:
        pass


@pytest.mark.anyio
async def test_run_one_handles_agent_error() -> None:
    s = Schedule(name="test", cron="* * * * * *", task_content="ping")
    agent = _RaisingAgent()
    with anyio.move_on_after(3):
        await ScheduleRegistry._run_one(s, cast(Any, agent))
