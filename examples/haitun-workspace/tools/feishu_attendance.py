"""Feishu/Lark attendance (考勤) tools — read clock results + admin config (read-only).

Query who clocked in/out over a date range (when, where, late/early/missing), and
read the admin-console config that clock results alone don't reveal: attendance
groups (考勤组 — punch method, schedule, bound shifts) and shifts (班次 — punch time
segments, flexible/late/early rules). Read-only — none of these clock in on
anyone's behalf or change any config.

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


async def feishu_attendance_groups(page_size: int = 50, page_token: str = "") -> str:
    """List attendance groups (考勤组) the app can see — read-only.

    An attendance group is the admin-console unit that binds people to a punch
    method, a schedule, and shifts. This returns only ``group_id`` + ``group_name``
    for each; pass a ``group_id`` to ``feishu_attendance_group_config`` for the full
    rule set. Paginated: if ``has_more`` is true, call again with the returned
    ``page_token``.

    Args:
        page_size: Page size, 1-50 (default 50).
        page_token: Leave empty on the first call; pass the returned token to page.
    """
    return _f.dumps_result(await _f.list_attendance_groups_impl(page_size, page_token))


async def feishu_attendance_group_config(
    group_id: str, employee_type: str = "employee_id", dept_type: str = "open_id"
) -> str:
    """Get one attendance group's full config (考勤组配置) — read-only.

    Returns the admin-console settings you can't see from clock results: punch
    method (``punch_type`` — GPS/Wi-Fi/machine/IP), 外勤打卡 (``allow_out_punch``),
    PC 打卡 (``allow_pc_punch``), 工作日不打卡记为缺卡 (``work_day_no_punch_as_lack``),
    the bound shift ids (``punch_day_shift_ids``), 排班特殊日期
    (``need_punch_special_days`` / ``no_need_punch_special_days``), 自由打卡窗口
    (``free_punch_cfg``), and overtime punch config (``overtime_clock_cfg``).

    Args:
        group_id: The group's id (from ``feishu_attendance_groups``).
        employee_type: ID type for user fields in the response — employee_id
            (default), employee_no, open_id, or union_id.
        dept_type: Department ID type — open_id (default) or department_id.
    """
    return _f.dumps_result(await _f.get_attendance_group_impl(group_id, employee_type, dept_type))


async def feishu_attendance_shifts(page_size: int = 50, page_token: str = "") -> str:
    """List attendance shifts (班次) the app can see — read-only.

    A shift defines the punch time segments and the late/early/flexible rules that
    a workday follows. This returns ``shift_id`` + ``shift_name`` + ``punch_times``
    + ``is_flexible`` for each; pass a ``shift_id`` to ``feishu_attendance_shift_config``
    for the full rule set. Paginated: if ``has_more`` is true, call again with the
    returned ``page_token``.

    Args:
        page_size: Page size, 1-50 (default 50).
        page_token: Leave empty on the first call; pass the returned token to page.
    """
    return _f.dumps_result(await _f.list_shifts_impl(page_size, page_token))


async def feishu_attendance_shift_config(shift_id: str) -> str:
    """Get one shift's full config (班次配置) — read-only.

    Returns exactly the admin settings the bot otherwise can't report: the punch
    time segments 打卡时间段 (``punch_time_rule`` — each has ``on_time`` / ``off_time``
    plus the ``late_minutes_as_late`` / ``early_minutes_as_early`` / lack thresholds
    and earliest-in / latest-out windows), the flexible rules 弹性规则
    (``is_flexible`` / ``flexible_minutes`` / ``flexible_rule`` with max early-leave
    and late-arrival minutes), rest segments (``rest_time_rule``), and overtime
    rules (``overtime_rule``).

    Args:
        shift_id: The shift's id (from ``feishu_attendance_shifts``, or a group's
            ``punch_day_shift_ids``).
    """
    return _f.dumps_result(await _f.get_shift_impl(shift_id))
