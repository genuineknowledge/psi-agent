from __future__ import annotations

import textwrap
from pathlib import Path

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
