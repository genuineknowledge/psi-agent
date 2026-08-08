"""Feishu Attendance (考勤) — query user tasks.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

# ── Attendance (考勤) — read clock-in/out results (read-only) ─────────────────
#
# Query attendance task results (who clocked in/out, when, where, and whether
# late/early/missing). Read-only — no proxy clock-in. Bot/tenant token works with
# the attendance:task:readonly scope; the app must be a Custom App and be granted
# a data-permission scope in the attendance admin console.


def _fmt_check_time(rec: Any) -> str:
    """Format a check record's check_time (epoch seconds string) to 'YYYY-MM-DD HH:MM:SS'."""
    if not isinstance(rec, dict):
        return ""
    ts = rec.get("check_time")
    if not ts:
        return ""
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError, OSError, OverflowError):
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def _build_user_tasks_query_request(
    user_ids: list[str], check_date_from: int, check_date_to: int, employee_type: str, need_overtime: bool
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/attendance/v1/user_tasks/query"
    req.add_query("employee_type", employee_type)
    req.add_query("ignore_invalid_users", "true")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "user_ids": user_ids,
        "check_date_from": check_date_from,
        "check_date_to": check_date_to,
        "need_overtime_result": need_overtime,
    }
    return req


async def query_attendance_impl(
    user_ids: str,
    date_from: str,
    date_to: str,
    employee_type: str = "employee_id",
    need_overtime: bool = False,
) -> dict[str, Any]:
    """Query attendance clock results for users over a date range (read-only)."""
    ids = [u.strip() for u in user_ids.split(",") if u.strip()]
    if not ids:
        return _core._error("No user_ids provided (comma-separated, max 50).")
    try:
        df = int(date_from.strip())
        dt = int(date_to.strip())
    except ValueError:
        return _core._error("date_from / date_to must be yyyyMMdd integers, e.g. 20260714.")
    res = await _core._invoke(_build_user_tasks_query_request(ids, df, dt, employee_type, need_overtime))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    results = []
    for r in data.get("user_task_results", []) if isinstance(data.get("user_task_results"), list) else []:
        # Each user_task_result has a "records" array with per-shift check-in/out
        records = r.get("records", []) if isinstance(r.get("records"), list) else []
        for rec in records:
            cin = rec.get("check_in_record", {}) if isinstance(rec.get("check_in_record"), dict) else {}
            cout = rec.get("check_out_record", {}) if isinstance(rec.get("check_out_record"), dict) else {}
            results.append(
                {
                    "user_id": r.get("user_id", ""),
                    "name": r.get("employee_name", ""),
                    "day": r.get("day", ""),
                    "check_in_time": _fmt_check_time(cin),
                    "check_in_result": rec.get("check_in_result", ""),
                    "check_in_location": cin.get("location_name", ""),
                    "check_out_time": _fmt_check_time(cout),
                    "check_out_result": rec.get("check_out_result", ""),
                    "check_out_location": cout.get("location_name", ""),
                }
            )
    return {
        "ok": True,
        "results": results,
        "count": len(results),
        "invalid_user_ids": data.get("invalid_user_ids", []),
        "unauthorized_user_ids": data.get("unauthorized_user_ids", []),
    }
