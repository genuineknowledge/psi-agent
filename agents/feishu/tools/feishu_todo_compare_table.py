"""Idempotently provision the per-cycle 个人对比 (dynamic layer-1) table.

The dynamic layer-1 ledger (前后对比:新开/承接/消失/已闭环/回流/请假顺延) lives as
ONE table per cycle in the same base as the TODO 台账 tables, named
``个人对比-<cycle_date>`` — so the base's left rail reads like a date picker:
台账-<date> (raw three-level items) next to 个人对比-<date> (per-person
continuity). This tool creates the table from a fixed field definition (成员/mentor
are PERSON-typed, numbers are NUMBER-typed) or returns the existing table_id —
row writes go through ``feishu_bitable_create_records`` afterwards.

Args:
    app_token: The base's app_token (the shared 台账 base).
    cycle_date: Cycle date (YYYY-MM-DD), used in the table name.
    user_key: The sender's open_id (from ``<feishu_context>``).
    identity: Who owns the write — ``"user"`` / ``"bot"`` (usual convention).
"""

from __future__ import annotations

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

_COMPARE_TABLE_PREFIX = "个人对比-"

# Fixed column definition — 成员/mentor are PERSON (11) columns, the six metrics
# are NUMBER (2) columns so the table can sum/aggregate, 结论/待确认 are text.
_COMPARE_SCHEMA_FIELDS: list[dict[str, object]] = [
    {"field_name": "周期日期", "type": 5},
    {"field_name": "成员", "type": 11},
    {"field_name": "mentor", "type": 11},
    {"field_name": "新开", "type": 2},
    {"field_name": "承接", "type": 2},
    {"field_name": "消失", "type": 2},
    {"field_name": "已闭环", "type": 2},
    {"field_name": "回流", "type": 2},
    {"field_name": "请假顺延", "type": 2},
    {"field_name": "结论", "type": 1},
    {"field_name": "待确认", "type": 1},
]


def _compare_table_name(cycle_date: str) -> str:
    return f"{_COMPARE_TABLE_PREFIX}{cycle_date.strip()}"


def _build_compare_table_request(app_token: str, table_name: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # Fields must live INSIDE the table object (same as mentor_ledger).
    req.body = {
        "table": {"name": table_name, "fields": _COMPARE_SCHEMA_FIELDS},
    }
    return req


async def feishu_todo_compare_table(
    app_token: str,
    cycle_date: str,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Ensure the ``个人对比-<cycle_date>`` table exists; return its table_id."""
    if not app_token.strip():
        return _core.dumps_result(_core._error("app_token is required (the shared 台账 base app_token)."))
    if not cycle_date.strip():
        return _core.dumps_result(_core._error("cycle_date is required (YYYY-MM-DD)."))

    list_res = await _core._invoke(
        _core._build_list_tables_request(app_token.strip()),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not list_res["ok"]:
        return _core.dumps_result(list_res)
    data = list_res["data"] if isinstance(list_res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    target = _compare_table_name(cycle_date)
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("name", "").strip() == target:
            table_id = item.get("table_id", "")
            if not table_id:
                return _core.dumps_result(_core._error(f"Table {target!r} found but its table_id was missing."))
            return _core.dumps_result({"ok": True, "table_id": table_id, "name": target, "created": False})

    create_res = await _core._invoke(
        _build_compare_table_request(app_token.strip(), target),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not create_res["ok"]:
        return _core.dumps_result(create_res)
    cdata = create_res["data"] if isinstance(create_res["data"], dict) else {}
    table_id = cdata.get("table_id", "")
    if not table_id:
        return _core.dumps_result(_core._error("Table creation succeeded but the response carried no table_id."))
    return _core.dumps_result({"ok": True, "table_id": table_id, "name": target, "created": True})
