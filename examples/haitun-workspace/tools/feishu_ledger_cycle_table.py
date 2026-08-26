"""Feishu Mentor Ledger — per-cycle table provisioning.

Backs the "each cycle gets its own ledger table" rule: every cycle, the sync
flow creates (or reuses) a table named ``台账-<cycle_date>`` inside the mentor's
existing ledger base, so a cycle's report opens onto only that cycle's rows —
mentor scores / comments from earlier cycles stay in their own tables and can
never leak into a later cycle's report.

Schema is owned by ``_feishu/mentor_ledger.py`` (``_LEDGER_SCHEMA_FIELDS``), the
same fixed column definition used for first-time provisioning, so every cycle
table starts with identical columns. Single-select options (层级/父项) come
empty from creation — company-todo-sync syncs them before writing rows, exactly
like the first table.
"""

from __future__ import annotations

import _feishu_impl as _core
from _feishu.mentor_ledger import _LEDGER_SCHEMA_FIELDS, _build_list_tables_request
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest


def _cycle_table_name(cycle_date: str) -> str:
    return f"台账-{cycle_date.strip()}"


def _build_cycle_table_request(app_token: str, table_name: str) -> BaseRequest:
    """Create-table request with the ledger schema.

    Fields must live **inside** the ``table`` object (``{"table": {"name": ...,
    "fields": [...]}}``) — Feishu silently ignores a top-level ``fields`` key and
    creates a table with only the default placeholder columns.
    """
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"table": {"name": table_name, "fields": _LEDGER_SCHEMA_FIELDS}}
    return req


async def feishu_mentor_ledger_cycle_table(
    app_token: str,
    cycle_date: str,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Idempotently provision (or fetch) the cycle's ledger table and return its table_id.

    ``app_token`` is the mentor's existing ledger base (from ``feishu_mentor_ledger_ensure``).
    ``cycle_date`` is the cycle date (YYYY-MM-DD), used in the table name ``台账-<cycle_date>``.
    If the table already exists its ``table_id`` is returned as-is (no duplicate tables);
    otherwise a new table is created from ``_LEDGER_SCHEMA_FIELDS``.

    Writes go through the user identity (``user_key`` + ``prefer="user"``) because
    the bot is usually not a collaborator on the base.
    """
    if not app_token.strip():
        return _core.dumps_result(_core._error("app_token is required (the mentor's ledger base app_token)."))
    if not cycle_date.strip():
        return _core.dumps_result(_core._error("cycle_date is required (YYYY-MM-DD)."))

    list_res = await _core._invoke(
        _build_list_tables_request(app_token.strip()),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not list_res["ok"]:
        return _core.dumps_result(list_res)
    data = list_res["data"] if isinstance(list_res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    target = _cycle_table_name(cycle_date)
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("name", "").strip() == target:
            table_id = item.get("table_id", "")
            if not table_id:
                return _core.dumps_result(
                    _core._error(f"Table {target!r} found but its table_id was missing: {item!r}")
                )
            return _core.dumps_result(
                {
                    "ok": True,
                    "table_id": table_id,
                    "name": target,
                    "created": False,
                }
            )

    create_res = await _core._invoke(
        _build_cycle_table_request(app_token.strip(), target),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not create_res["ok"]:
        return _core.dumps_result(create_res)
    cdata = create_res["data"] if isinstance(create_res["data"], dict) else {}
    table_id = cdata.get("table_id", "")
    if not table_id:
        return _core.dumps_result(
            _core._error(f"Table creation succeeded but the response carried no table_id: {cdata!r}")
        )
    return _core.dumps_result(
        {
            "ok": True,
            "table_id": table_id,
            "name": target,
            "created": True,
            "schema_fields": len(_LEDGER_SCHEMA_FIELDS),
        }
    )
