"""Feishu/Lark calendar tool — create a calendar event (日程).

Create an event on the bot's own primary calendar, optionally inviting people.
The bot is the organizer; attendees get a Feishu notification.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``, the app to have bot
ability enabled (else error 190007), and the ``calendar:calendar`` scope. To
invite people, resolve their open_id first (e.g. via ``feishu_chat_find_member``).
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
