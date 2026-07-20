"""Feishu/Lark calendar tools — read schedules and create events (日程).

Three capabilities on the bot's calendar:
- ``feishu_calendar_create_event`` — create one event, optionally inviting several
  people to the *same* meeting (they all share one event).
- ``feishu_calendar_list_events`` — read the schedule: list events on a calendar
  between two instants. Reading another calendar needs reader access to it.
- ``feishu_calendar_create_per_person`` — give each person their *own* schedule:
  create one independent event per attendee, each inviting only that one person.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``, the app to have bot
ability enabled (else error 190007), and a calendar scope (``calendar:calendar``
or ``calendar:calendar.event:read`` for read-only). To target people, resolve
their open_id first (e.g. via ``feishu_chat_find_member`` or
``feishu_department_members``).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_calendar_create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    attendees: str = "",
    timezone: str = "Asia/Shanghai",
) -> str:
    """Create a Feishu calendar event on the bot's primary calendar.

    Returns the new ``event_id`` and ``calendar_id``. If attendees are given they
    are invited in a follow-up call; a partial ``attendee_warning`` is returned if
    that step fails (the event is still created).

    Args:
        summary: Event title.
        start: Start — 'YYYY-MM-DD HH:MM' (timed) or 'YYYY-MM-DD' (all-day).
        end: End — same formats as start.
        description: Optional description (HTML allowed).
        attendees: Comma-separated open_ids to invite (optional).
        timezone: IANA timezone, default Asia/Shanghai.
    """
    return _f.dumps_result(await _f.create_event_impl(summary, start, end, description, attendees, timezone))


async def feishu_calendar_list_events(
    start: str,
    end: str,
    calendar_id: str = "",
    timezone: str = "Asia/Shanghai",
    max_events: int = 50,
) -> str:
    """List events (read the schedule) on a Feishu calendar between start and end.

    Returns ``count`` and ``events``, each ``{event_id, summary, description,
    start, end, status, is_all_day, organizer, attendee_ability}``. Leave
    ``calendar_id`` blank to read the bot's own primary calendar; to read another
    calendar the identity must have reader access to it.

    Args:
        start: Range start — 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' (00:00 that day).
        end: Range end — same formats as start.
        calendar_id: Target calendar_id. Blank uses the bot's primary calendar.
        timezone: IANA timezone used to interpret start/end, default Asia/Shanghai.
        max_events: Max events to return (pages automatically), default 50.
    """
    return _f.dumps_result(await _f.list_events_impl(start, end, calendar_id, timezone, max_events))


async def feishu_calendar_create_per_person(
    summary: str,
    start: str,
    end: str,
    attendees: str,
    description: str = "",
    timezone: str = "Asia/Shanghai",
) -> str:
    """Give each person their own schedule: create one independent event per attendee.

    Unlike ``feishu_calendar_create_event`` (one shared meeting for everyone),
    this creates a *separate* event per open_id on the bot's primary calendar,
    each inviting only that one person. Use it to set individual schedules (e.g.
    per-person shifts or tasks). Resolve open_ids first via
    ``feishu_chat_find_member`` / ``feishu_department_members``.

    Returns ``created`` and ``failed`` lists (per person); partial failures do not
    abort the others, and ``ok`` is true only if every person succeeded.

    Args:
        summary: Event title (same for each person).
        start: Start — 'YYYY-MM-DD HH:MM' (timed) or 'YYYY-MM-DD' (all-day).
        end: End — same formats as start.
        attendees: Comma-separated open_ids; one event is created for each.
        description: Optional description (HTML allowed).
        timezone: IANA timezone, default Asia/Shanghai.
    """
    return _f.dumps_result(
        await _f.create_events_per_person_impl(summary, start, end, attendees, description, timezone)
    )
