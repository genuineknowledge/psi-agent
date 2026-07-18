"""Feishu/Lark attendance (考勤) tool — read clock-in/out results (read-only).

Query who clocked in/out over a date range, when, where, and whether they were
late/early/missing. Read-only — this does NOT clock in on anyone's behalf.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``, the app to be a
**Custom App** with the ``attendance:task:readonly`` scope, and a data-permission
scope granted in the Feishu attendance admin console (else 1220004/1220005).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_attendance_query(
    user_ids: str,
    date_from: str,
    date_to: str,
    employee_type: str = "employee_id",
    need_overtime: bool = False,
) -> str:
    """Query attendance clock-in/out results for users over a date range (read-only).

    Returns per-user-per-day results: ``check_in_time`` / ``check_out_time`` (local
    time), ``check_in_result`` / ``check_out_result`` (Normal / Early / Late / Lack …),
    and locations. Also returns ``invalid_user_ids`` / ``unauthorized_user_ids`` for
    users that couldn't be resolved or aren't in the app's data scope.

    Args:
        user_ids: Comma-separated user IDs (max 50), matching ``employee_type``.
        date_from: Start date, yyyyMMdd (e.g. 20260714).
        date_to: End date, yyyyMMdd (inclusive).
        employee_type: ID type of user_ids — employee_id (default), employee_no,
            open_id, or union_id.
        need_overtime: Include overtime shift segments (default False).
    """
    return _f.dumps_result(await _f.query_attendance_impl(user_ids, date_from, date_to, employee_type, need_overtime))
