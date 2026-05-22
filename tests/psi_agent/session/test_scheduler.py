from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest

from psi_agent.session.scheduler import Schedule, _parse_yaml_header, load_schedules_from_workspace


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

    schedules = await load_schedules_from_workspace(tmp_path / "schedules")
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

    schedules = await load_schedules_from_workspace(tmp_path / "schedules")
    assert len(schedules) == 0


@pytest.mark.anyio
async def test_load_multiple_schedules(tmp_path: Path) -> None:
    for name in ["daily", "weekly"]:
        d = tmp_path / "schedules" / name
        d.mkdir(parents=True)
        (d / "TASK.md").write_text(f'---\nname: {name}\ncron: "0 12 * * *"\n---\nTask: {name}')

    schedules = await load_schedules_from_workspace(tmp_path / "schedules")
    assert len(schedules) == 2
    names = {s.name for s in schedules}
    assert names == {"daily", "weekly"}


@pytest.mark.anyio
async def test_load_schedules_missing_dir(tmp_path: Path) -> None:
    schedules = await load_schedules_from_workspace(tmp_path / "nonexistent")
    assert len(schedules) == 0


@pytest.mark.anyio
async def test_load_schedule_missing_name(tmp_path: Path) -> None:
    schedules_dir = tmp_path / "schedules" / "bad"
    schedules_dir.mkdir(parents=True)
    (schedules_dir / "TASK.md").write_text('---\ncron: "0 12 * * *"\n---\nTask')

    schedules = await load_schedules_from_workspace(tmp_path / "schedules")
    assert len(schedules) == 0


def test_schedule_to_user_message() -> None:
    s = Schedule(name="test-schedule", cron="* * * * *", task_content="Run the report")
    msg = s.to_user_message()
    assert msg["role"] == "user"
    assert "[Schedule Task: test-schedule]" in msg["content"]
    assert "Run the report" in msg["content"]


def test_schedule_should_run_now() -> None:
    s = Schedule(name="every-min", cron="* * * * *", task_content="run")
    assert not s.should_run_now()
    s._last_run = time.time() - 3600
    assert s.should_run_now()


def test_schedule_get_next_run() -> None:
    s = Schedule(name="daily", cron="0 12 * * *", task_content="run")
    next_run = s.get_next_run()
    assert next_run is not None
    assert next_run > time.time()


def test_schedule_pending_response() -> None:
    s = Schedule(name="test", cron="* * * * *", task_content="run")
    assert s.pending_response is None
    s.pending_response = [{"key": "value"}]
    assert s.pending_response == [{"key": "value"}]
    assert s.has_pending
    s.clear_pending()
    assert s.pending_response is None
    assert not s.has_pending

# --- Missing coverage tests ---


def test_parse_yaml_header_error() -> None:
    # Malformed YAML
    content = "---\n: invalid yaml: :\n---\nbody"
    header, body = _parse_yaml_header(content)
    assert header is None
    assert body == content


def test_schedule_invalid_cron_raises() -> None:

    with pytest.raises(ValueError):
        Schedule(name="bad", cron="not a cron", task_content="run")


def test_schedule_mark_run_prevents_should_run() -> None:

    s = Schedule(name="every-min", cron="* * * * *", task_content="run")
    # Force a past run time to simulate having just run
    s._last_run = time.time()
    assert not s.should_run_now()


def test_get_prev_run() -> None:
    s = Schedule(name="daily", cron="0 12 * * *", task_content="run")
    prev = s.get_prev_run()
    assert prev is not None
    # get_prev_run should be < current time
    assert prev < time.time()


def test_pending_response_empty_list() -> None:
    s = Schedule(name="test", cron="* * * * *", task_content="run")
    # has_pending checks `is not None` — empty list IS a pending response
    s.pending_response = []
    assert s.has_pending
    s.pending_response = None
    assert not s.has_pending
