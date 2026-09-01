"""Tests for the knowledge-cutoff line in _build_datetime_section."""

from __future__ import annotations

import importlib
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = WORKSPACE_ROOT / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

system: Any = importlib.import_module("system")


def test_cutoff_line_present_when_env_set(monkeypatch):
    monkeypatch.setenv("HAITUN_KNOWLEDGE_CUTOFF", "2026-01")
    out = system._build_datetime_section()
    assert "Knowledge cutoff: 2026-01" in out
    assert "verify online" in out


def test_cutoff_line_neutral_when_env_unset(monkeypatch):
    monkeypatch.delenv("HAITUN_KNOWLEDGE_CUTOFF", raising=False)
    out = system._build_datetime_section()
    assert "Knowledge cutoff: unknown" in out
    # never fabricate a date on the cutoff line when unset. Check the cutoff
    # line itself rather than a bare "YYYY-MM" literal, which could legitimately
    # collide with the live "Date:" line in some months.
    cutoff_line = next(line for line in out.splitlines() if line.startswith("Knowledge cutoff:"))
    assert not re.search(r"\d{4}-\d{2}", cutoff_line)


def test_current_date_still_present(monkeypatch):
    monkeypatch.delenv("HAITUN_KNOWLEDGE_CUTOFF", raising=False)
    out = system._build_datetime_section()
    assert "## Current Date & Time" in out
    assert "Date:" in out


# -- the weekday is computed and printed, never left to the model --------------
#
# The block used to carry a bare ``Date: 2026-08-03``, so "是周几" and "周一晚上
# 8:30 是哪天" were both left to recall. In production the agent called
# 2026-08-03 a Sunday, corrected itself to Monday one turn later, and built a
# schedule table off the wrong day. Printing the weekday removes the guess.


def _date_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("Date:"))


def test_date_line_states_the_weekday(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    line = _date_line(system._build_datetime_section())

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    assert today.strftime("%Y-%m-%d") in line
    assert today.strftime("%A") in line
    assert "周" + "一二三四五六日"[today.weekday()] in line


def test_weekday_label_is_correct_for_the_reported_regression():
    """2026-08-03 is a Monday and 2026-08-02 a Sunday — the pair that got swapped."""
    assert system._weekday_label(date(2026, 8, 3)) == "2026-08-03 Monday 周一"
    assert system._weekday_label(date(2026, 8, 2)) == "2026-08-02 Sunday 周日"
    assert system._weekday_label(date(2026, 8, 4)) == "2026-08-04 Tuesday 周二"


def test_calendar_table_covers_yesterday_through_next_week():
    """ "下周二" has to be resolvable by reading a row, so the span must reach it."""
    today = date(2026, 8, 3)
    lines = system._build_calendar_lines(today)

    assert any("(TODAY)" in ln for ln in lines)
    assert any("(yesterday)" in ln for ln in lines)
    assert any("(tomorrow)" in ln for ln in lines)
    # Yesterday plus today plus the next 7 days.
    assert len(lines) == 9
    for offset in range(-1, 8):
        day = today + timedelta(days=offset)
        assert any(day.strftime("%Y-%m-%d") in ln for ln in lines), day

    today_line = next(ln for ln in lines if "(TODAY)" in ln)
    assert "2026-08-03" in today_line and "Monday" in today_line
    tomorrow_line = next(ln for ln in lines if "(tomorrow)" in ln)
    assert "2026-08-04" in tomorrow_line and "Tuesday" in tomorrow_line


def test_calendar_weekdays_are_all_internally_consistent():
    """Every printed row must agree with the calendar, not just the anchors."""
    for start in (date(2026, 8, 3), date(2026, 12, 31), date(2027, 2, 26)):
        lines = system._build_calendar_lines(start)
        for offset, line in zip(range(-1, 8), lines, strict=True):
            day = start + timedelta(days=offset)
            assert day.strftime("%Y-%m-%d") in line
            assert day.strftime("%A") in line


def test_section_marks_itself_as_this_turn(monkeypatch):
    """Past turns keep their own block; the live one must say it wins."""
    out = system._build_datetime_section()
    heading = out.splitlines()[0]

    assert heading.startswith("## Current Date & Time")
    assert "THIS TURN" in heading
    assert "ignore any earlier" in heading.lower() or "ignore" in heading.lower()


def test_calendar_follows_the_clock_source(monkeypatch):
    """The table is anchored to the same zone as the Date: line, not to UTC."""
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    out = system._build_datetime_section()
    today = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()

    today_line = next(ln for ln in out.splitlines() if "(TODAY)" in ln)
    assert today.strftime("%Y-%m-%d") in today_line
