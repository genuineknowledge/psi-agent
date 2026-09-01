"""Feishu Bitable (多维表格) — records CRUD, search, fields, tables.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest


def _parse_resp_body(resp: Any) -> dict[str, Any]:
    """Extract the JSON body dict from an SDK BaseResponse (raw.content bytes)."""
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            parsed = json.loads(bytes(content).decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
    code = getattr(resp, "code", None)
    return {"code": code, "msg": getattr(resp, "msg", "") or ""}


# ── Bitable (多维表格) — list tables, list/create records ─────────────────────
#
# Generic read/write for Feishu bases; the bot's tenant token can read+write
# records provided the app is a collaborator on the base (scope bitable:app).
# app_token is the segment in a feishu.cn/base/<app_token> URL (for wiki links,
# resolve via feishu_api on /open-apis/wiki/v2/spaces/get_node — obj_token is the
# app_token when obj_type=bitable).


def _build_list_records_request(
    app_token: str, table_id: str, page_size: int, page_token: str, filter_: str, sort: str, field_names: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    if filter_:
        req.add_query("filter", filter_)
    if sort:
        req.add_query("sort", sort)
    if field_names:
        req.add_query("field_names", field_names)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


# ── Bitable reads — conditional search and single-record fetch ────────────────
#
# list_bitable_records above is the plain GET: it pages the whole table (or a
# view) and its query-string `filter` only covers simple cases. The search
# endpoint is a POST whose body carries structured conditions, and Feishu's own
# docs point at it as the way to obtain a record_id — which is exactly what the
# update/delete tools need. Note the endpoint ignores `view_id` as soon as filter
# or sort is given: the request then applies to the whole table.

_SEARCH_OPERATORS = (
    "is",
    "isNot",
    "contains",
    "doesNotContain",
    "isEmpty",
    "isNotEmpty",
    "isGreater",
    "isGreaterEqual",
    "isLess",
    "isLessEqual",
)


def _build_search_records_request(
    app_token: str, table_id: str, body: dict[str, Any], page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/search"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


def _parse_search_filter(filter_json: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and check a search filter object; return (filter, error message)."""
    try:
        parsed = json.loads(filter_json)
    except ValueError as exc:
        return None, f"filter_json is not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, (
            'filter_json must be a JSON object, e.g. \'{"conjunction":"and","conditions":'
            '[{"field_name":"状态","operator":"is","value":["进行中"]}]}\'.'
        )
    conjunction = str(parsed.get("conjunction", "")).strip().lower()
    if conjunction not in {"and", "or"}:
        return None, 'filter_json needs "conjunction": "and" or "or" (Feishu requires it).'
    parsed["conjunction"] = conjunction
    conditions = parsed.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return None, 'filter_json needs a non-empty "conditions" array.'
    if len(conditions) > 50:
        return None, f"filter_json has {len(conditions)} conditions; Feishu allows at most 50."
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            return None, f"filter_json.conditions[{i}] must be an object with field_name and operator."
        if not str(cond.get("field_name", "")).strip():
            return None, f"filter_json.conditions[{i}] is missing a non-empty field_name."
        operator = str(cond.get("operator", "")).strip()
        if operator not in _SEARCH_OPERATORS:
            return None, (
                f"filter_json.conditions[{i}].operator {operator!r} is not supported; "
                f"use one of {', '.join(_SEARCH_OPERATORS)}."
            )
        value = cond.get("value")
        if value is not None and not isinstance(value, list):
            return None, (
                f'filter_json.conditions[{i}].value must be an array of strings, e.g. ["进行中"] '
                "(omit it for isEmpty / isNotEmpty)."
            )
    return parsed, None


async def search_bitable_records_impl(
    app_token: str,
    table_id: str,
    filter_json: str = "",
    sort_json: str = "",
    field_names: str = "",
    view_id: str = "",
    page_size: int = 100,
    page_token: str = "",
    automatic_fields: bool = False,
    user_key: str = "",
) -> dict[str, Any]:
    """Search records with structured conditions. Returns [{record_id, fields}] + pagination."""
    if not app_token.strip():
        return _core._error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _core._error(
            "No table_id provided (get it from feishu_api GET /open-apis/bitable/v1/apps/:app_token/tables)."
        )
    if page_size < 1 or page_size > 500:
        return _core._error(f"page_size must be between 1 and 500 (got {page_size}).")
    body: dict[str, Any] = {}
    if filter_json.strip():
        parsed_filter, problem = _parse_search_filter(filter_json)
        if problem:
            return _core._error(problem)
        body["filter"] = parsed_filter
    if sort_json.strip():
        try:
            sort = json.loads(sort_json)
        except ValueError as exc:
            return _core._error(f"sort_json is not valid JSON: {exc}")
        if not isinstance(sort, list):
            return _core._error('sort_json must be a JSON array, e.g. \'[{"field_name":"日期","desc":true}]\'.')
        body["sort"] = sort
    if field_names.strip():
        try:
            names = json.loads(field_names)
        except ValueError as exc:
            return _core._error(f"field_names is not valid JSON: {exc}")
        if not isinstance(names, list):
            return _core._error('field_names must be a JSON array of column names, e.g. \'["状态","负责人"]\'.')
        body["field_names"] = names
    if view_id.strip():
        if "filter" in body or "sort" in body:
            # Feishu silently ignores view_id here; say so rather than let the caller
            # believe the search was scoped to their view.
            return _core._error(
                "view_id cannot be combined with filter_json / sort_json — Feishu then searches the "
                "whole table and ignores the view. Drop one of them."
            )
        body["view_id"] = view_id.strip()
    if automatic_fields:
        body["automatic_fields"] = True
    res = await _core._invoke(
        _build_search_records_request(app_token.strip(), table_id.strip(), body, page_size, page_token),
        user_key=user_key,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    records = [
        {"record_id": r.get("record_id", ""), "fields": r.get("fields", {})}
        for r in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
        "total": data.get("total", 0),
    }


def _build_batch_create_records_request(app_token: str, table_id: str, records: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_create"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": records}
    return req


async def create_bitable_records_impl(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Create many records in one call. records_json is a JSON array of row objects; batches of 500."""
    if not app_token.strip():
        return _core._error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _core._error(
            "No table_id provided (get it from feishu_api GET /open-apis/bitable/v1/apps/:app_token/tables)."
        )
    try:
        rows = json.loads(records_json)
    except ValueError as exc:
        return _core._error(f"records_json is not valid JSON: {exc}")
    if not isinstance(rows, list) or not rows:
        return _core._error(
            "records_json must be a non-empty JSON array of row objects, e.g. "
            '\'[{"姓名":"张三","状态":"在读"},{"姓名":"李四"}]\'.'
        )
    # Accept both the bare {column: value} shape and Feishu's {"fields": {...}} wrapper,
    # since a caller who just used update_records will reach for the latter.
    records: list[dict[str, Any]] = []
    names: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return _core._error(f"records_json[{i}] must be a JSON object of column → value.")
        wrapped = row.get("fields")
        fields = _as_field_map(wrapped) if isinstance(wrapped, dict) else _as_field_map(row)
        if not fields:
            return _core._error(f"records_json[{i}] has no column values.")
        records.append({"fields": fields})
        names.extend(k for k in fields if k not in names)
    if validate_fields:
        problem = await _check_bitable_columns(app_token.strip(), table_id.strip(), names)
        if problem:
            return problem
    created: list[str] = []
    dropped: list[str] = []
    for i in range(0, len(records), 500):
        batch = records[i : i + 500]
        res = await _core._invoke(
            _build_batch_create_records_request(app_token.strip(), table_id.strip(), batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "created": created, "count": len(created)}
        data = res["data"] if isinstance(res["data"], dict) else {}
        echoed = data.get("records", []) if isinstance(data.get("records"), list) else []
        for offset, rec in enumerate(echoed):
            if not isinstance(rec, dict):
                continue
            created.append(rec.get("record_id", ""))
            if offset < len(batch):
                dropped.extend(_dropped_fields(batch[offset]["fields"], rec.get("fields", {})))
    result: dict[str, Any] = {"ok": True, "created": created, "count": len(created)}
    if dropped:
        result["dropped_fields"] = sorted(set(dropped))
        result["warning"] = (
            f"Feishu accepted the call but did not write {', '.join(sorted(set(dropped)))} — "
            "check the column names and value types."
        )
    return result


# ── Bitable record updates — change cell values in existing rows ──────────────
#
# The update APIs are *incremental*: only the field names present in `fields` are
# written, everything else on the row keeps its value, and an explicit null blanks
# a cell. That is what makes "改一个单元格" possible without re-sending the row.
#
# The hazard these two impls guard against: Feishu **silently drops** field names
# the table doesn't have and still answers code:0. A caller who writes "Mentor"
# into a table whose column is "导师" gets a cheerful success and an unchanged
# cell. So the column names are checked against the table's real fields before the
# write, and the response is compared with what was asked for afterwards.


def _build_update_record_request(app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["record_id"] = record_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"fields": fields}
    return req


def _build_batch_update_records_request(app_token: str, table_id: str, records: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_update"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": records}
    return req


def _as_field_map(value: Any) -> dict[str, Any]:
    """Read a parsed-JSON object as a {column: value} map.

    ``json.loads`` is typed as ``Any``, so an ``isinstance(x, dict)`` check leaves the
    key type unknown and every column name downstream types as ``object``. JSON object
    keys are always strings, so restating that here keeps the callers plainly typed
    instead of casting at each use.
    """
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


async def _check_bitable_columns(app_token: str, table_id: str, names: list[str]) -> dict[str, Any] | None:
    """Reject column names the table doesn't have; return an error dict, or None if fine.

    Returns None as well when the field list can't be read (e.g. the bot may write but
    not list fields) — a failed *check* must not block a write the user asked for.
    """
    listed = await list_bitable_fields_impl(app_token, table_id)
    if not listed.get("ok"):
        return None
    valid = [f.get("name", "") for f in listed.get("fields", [])]
    unknown = [n for n in names if n not in valid]
    if not unknown:
        return None
    return _core._error(
        f"These column names are not in the table and would be silently ignored by Feishu: "
        f"{', '.join(unknown)}. Existing columns: {', '.join(valid)}.",
        unknown_fields=unknown,
        valid_fields=valid,
    )


def _dropped_fields(requested: dict[str, Any], written: Any) -> list[str]:
    """Field names asked for but missing from Feishu's echo of the updated record."""
    if not isinstance(written, dict):
        return []
    return [k for k, v in requested.items() if v is not None and k not in written]


async def update_bitable_record_impl(
    app_token: str,
    table_id: str,
    record_id: str,
    fields_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Update cells in one existing record. Only the given columns change; null clears one."""
    if not app_token.strip():
        return _core._error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _core._error(
            "No table_id provided (get it from feishu_api GET /open-apis/bitable/v1/apps/:app_token/tables)."
        )
    if not record_id.strip():
        return _core._error("No record_id provided (get it from feishu_bitable_search_records).")
    try:
        parsed = json.loads(fields_json)
    except ValueError as exc:
        return _core._error(f"fields_json is not valid JSON: {exc}")
    if not isinstance(parsed, dict) or not parsed:
        return _core._error(
            "fields_json must be a non-empty JSON object mapping column names to new values, "
            'e.g. \'{"状态":"已完成"}\'.'
        )
    fields = _as_field_map(parsed)
    if validate_fields:
        problem = await _check_bitable_columns(app_token.strip(), table_id.strip(), list(fields))
        if problem:
            return problem
    res = await _core._invoke(
        _build_update_record_request(app_token.strip(), table_id.strip(), record_id.strip(), fields),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    record = data.get("record", {}) if isinstance(data.get("record"), dict) else {}
    written = record.get("fields", {})
    result = {
        "ok": True,
        "record_id": record.get("record_id", "") or record_id.strip(),
        "updated_fields": list(fields),
        "fields": written,
    }
    dropped = _dropped_fields(fields, written)
    if dropped:
        result["dropped_fields"] = dropped
        result["warning"] = (
            f"Feishu accepted the call but did not write {', '.join(dropped)} — check the column names and value types."
        )
    return result


async def update_bitable_records_impl(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Update many records in one go. records_json is [{record_id, fields}]; batches of 1000."""
    if not app_token.strip():
        return _core._error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _core._error(
            "No table_id provided (get it from feishu_api GET /open-apis/bitable/v1/apps/:app_token/tables)."
        )
    try:
        parsed = json.loads(records_json)
    except ValueError as exc:
        return _core._error(f"records_json is not valid JSON: {exc}")
    if not isinstance(parsed, list) or not parsed:
        return _core._error(
            'records_json must be a non-empty JSON array, e.g. \'[{"record_id":"recA","fields":{"状态":"已完成"}}]\'.'
        )
    records: list[dict[str, Any]] = []
    names: list[str] = []
    for i, rec in enumerate(parsed):
        if not isinstance(rec, dict):
            return _core._error(f"records_json[{i}] must be a JSON object with record_id and fields.")
        record_id = str(rec.get("record_id", "")).strip()
        if not record_id:
            return _core._error(f"records_json[{i}] is missing a non-empty record_id.")
        raw_fields = rec.get("fields")
        if not isinstance(raw_fields, dict) or not raw_fields:
            return _core._error(f"records_json[{i}].fields must be a non-empty object of column → new value.")
        fields = _as_field_map(raw_fields)
        records.append({"record_id": record_id, "fields": fields})
        names.extend(k for k in fields if k not in names)
    if validate_fields:
        problem = await _check_bitable_columns(app_token.strip(), table_id.strip(), names)
        if problem:
            return problem
    updated: list[str] = []
    dropped: list[str] = []
    for i in range(0, len(records), 1000):
        batch = records[i : i + 1000]
        res = await _core._invoke(
            _build_batch_update_records_request(app_token.strip(), table_id.strip(), batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "updated": updated, "count": len(updated)}
        data = res["data"] if isinstance(res["data"], dict) else {}
        echoed = data.get("records", []) if isinstance(data.get("records"), list) else []
        by_id = {r.get("record_id", ""): r.get("fields", {}) for r in echoed if isinstance(r, dict)}
        for rec in batch:
            rid = str(rec["record_id"])
            updated.append(rid)
            if rid in by_id:
                dropped.extend(f"{rid}.{n}" for n in _dropped_fields(_as_field_map(rec["fields"]), by_id[rid]))
    result: dict[str, Any] = {"ok": True, "updated": updated, "count": len(updated)}
    if dropped:
        result["dropped_fields"] = dropped
        result["warning"] = (
            f"Feishu accepted the call but did not write {len(dropped)} value(s) — "
            "check the column names and value types."
        )
    return result


def _build_batch_delete_records_request(app_token: str, table_id: str, record_ids: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_delete"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": record_ids}
    return req


async def clear_bitable_table_impl(
    app_token: str,
    table_id: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Delete ALL records (rows) in a table — pages through every record, then batch-deletes."""
    ids: list[str] = []
    page_token = ""
    while True:
        res = await _core._invoke(
            _build_list_records_request(app_token, table_id, 500, page_token, "", "", ""), user_key=user_key
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for r in data.get("items", []) if isinstance(data.get("items"), list) else []:
            rid = r.get("record_id", "")
            if rid:
                ids.append(rid)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    if not ids:
        return {"ok": True, "deleted": 0, "note": "table already has no records"}
    deleted = 0
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        res = await _core._invoke(
            _build_batch_delete_records_request(app_token, table_id, batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "deleted": deleted}
        deleted += len(batch)
    return {"ok": True, "deleted": deleted}


_BITABLE_FIELD_TYPES = {
    1: "文本",
    2: "数字",
    3: "单选",
    4: "多选",
    5: "日期",
    7: "复选框",
    11: "人员",
    13: "电话",
    15: "超链接",
    17: "附件",
    18: "单向关联",
    20: "公式",
    21: "双向关联",
    22: "地理位置",
    23: "群组",
    1001: "创建时间",
    1002: "最后更新时间",
    1003: "创建人",
    1004: "修改人",
    1005: "自动编号",
}


def _build_list_fields_request(app_token: str, table_id: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_fields_impl(app_token: str, table_id: str) -> dict[str, Any]:
    """List a table's fields (columns). Returns [{field_id, name, type, is_primary}] for all fields."""
    fields: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _core._invoke(_build_list_fields_request(app_token, table_id, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for f in data.get("items", []) if isinstance(data.get("items"), list) else []:
            ftype = f.get("type")
            fields.append(
                {
                    "field_id": f.get("field_id", ""),
                    "name": f.get("field_name", ""),
                    "type": _BITABLE_FIELD_TYPES.get(ftype, ftype),
                    "is_primary": bool(f.get("is_primary")),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "fields": fields, "count": len(fields)}


# ── Bitable creation — new base, new data table, new field ────────────────────
#
# The tools above all need an app_token that already exists, i.e. a base somebody
# built by hand. These three create it: base (POST /bitable/v1/apps) → data table
# (POST .../tables, optionally with its initial columns) → extra field
# (POST .../fields). Writes prefer the user's identity so the base is owned by the
# person who asked for it, falling back to the bot's tenant token.
#
# Field `type` is the same integer vocabulary list_bitable_fields decodes:
# 1 文本, 2 数字, 3 单选, 4 多选, 5 日期, 7 复选框, 11 人员, 13 电话, 15 超链接,
# 17 附件, 18 单向关联, 20 公式, 21 双向关联, 22 地理位置, 23 群组, 1001 创建时间,
# 1002 最后更新时间, 1003 创建人, 1004 修改人, 1005 自动编号. Type 19 (查找引用)
# cannot be created. The first field of a table is its index (primary) column and
# only accepts 1, 2, 5, 13, 15, 20, 22 — Feishu answers 1254012 otherwise.

_INDEX_FIELD_TYPES = {1, 2, 5, 13, 15, 20, 22}
_UNCREATABLE_FIELD_TYPE = 19


def _validate_bitable_fields(fields: Any, *, as_table_fields: bool) -> str | None:
    """Check a parsed fields list; return an error message, or None when it is usable."""
    if not isinstance(fields, list) or not fields:
        return "fields_json must be a non-empty JSON array of field objects."
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            return f"fields_json[{i}] must be a JSON object with field_name and type."
        if not str(f.get("field_name", "")).strip():
            return f"fields_json[{i}] is missing a non-empty field_name."
        ftype = f.get("type")
        if not isinstance(ftype, int) or isinstance(ftype, bool):
            return f"fields_json[{i}].type must be an integer field type (1=文本, 2=数字, 3=单选, 5=日期, ...)."
        if ftype == _UNCREATABLE_FIELD_TYPE:
            return f"fields_json[{i}].type 19 (查找引用) cannot be created via the API."
        if as_table_fields and i == 0 and ftype not in _INDEX_FIELD_TYPES:
            return (
                f"fields_json[0].type {ftype} cannot be the index (primary) column; "
                f"the first field must be one of {sorted(_INDEX_FIELD_TYPES)} "
                "(1=文本, 2=数字, 5=日期, 13=电话, 15=超链接, 20=公式, 22=地理位置)."
            )
    return None


def _build_create_table_request(app_token: str, table: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"table": table}
    return req


async def create_bitable_table_impl(
    app_token: str,
    table_name: str,
    fields_json: str = "",
    default_view_name: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Create a data table in a bitable. fields_json is a JSON array of field objects."""
    if not app_token.strip():
        return _core._error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_name.strip():
        return _core._error("No table_name provided.")
    table: dict[str, Any] = {"name": table_name.strip()}
    if fields_json.strip():
        try:
            fields = json.loads(fields_json)
        except ValueError as exc:
            return _core._error(f"fields_json is not valid JSON: {exc}")
        problem = _validate_bitable_fields(fields, as_table_fields=True)
        if problem:
            return _core._error(problem)
        table["fields"] = fields
    if default_view_name.strip():
        if "fields" not in table:
            # Feishu rejects default_view_name on its own; say so instead of failing upstream.
            return _core._error("default_view_name requires fields_json (Feishu only accepts the two together).")
        table["default_view_name"] = default_view_name.strip()
    res = await _core._invoke(
        _build_create_table_request(app_token.strip(), table), user_key=user_key, prefer="user", identity=identity
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    field_ids = data.get("field_id_list", [])
    return {
        "ok": True,
        "table_id": data.get("table_id", ""),
        "name": table["name"],
        "default_view_id": data.get("default_view_id", ""),
        "field_ids": field_ids if isinstance(field_ids, list) else [],
    }


# ── Bitable base metadata — read, rename / toggle advanced perms, copy ─────────
#
# App-level rather than table-level: the metadata call is also how you check
# `is_advanced` before trying to create a role (advanced permission must be on),
# and `copy` turns an existing base into a template — a standard ledger built once
# and duplicated per project instead of rebuilt column by column.


def _build_update_field_request(app_token: str, table_id: str, field_id: str, field: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["field_id"] = field_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = field
    return req


async def update_bitable_field_impl(
    app_token: str,
    table_id: str,
    field_id: str,
    field_name: str = "",
    field_type: int = 0,
    property_json: str = "",
    ui_type: str = "",
    is_primary: bool | None = None,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Change a column's definition (rename, retype, edit its options). Keeps the column's data."""
    if not app_token.strip():
        return _core._error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _core._error(
            "No table_id provided (get it from feishu_api GET /open-apis/bitable/v1/apps/:app_token/tables)."
        )
    if not field_id.strip():
        return _core._error(
            "No field_id provided (get it from feishu_api GET .../bitable/v1/apps/:app_token/tables/:table_id/fields)."
        )
    # Feishu's update is a FULL replace of the field definition and demands both
    # field_name and type, so anything the caller left out is read back from the
    # table rather than silently reset to a default.
    current: dict[str, Any] = {}
    if not field_name.strip() or not field_type:
        listed = await _core._invoke(
            _build_list_fields_request(app_token.strip(), table_id.strip(), 100, ""), user_key=user_key
        )
        if not listed["ok"]:
            return _core._error(
                "field_name and field_type are both required (Feishu replaces the whole field "
                f"definition), and reading the current one failed: {listed.get('message', '')}"
            )
        data = listed["data"] if isinstance(listed["data"], dict) else {}
        for f in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if f.get("field_id") == field_id.strip():
                current = f
                break
        if not current:
            return _core._error(
                f"field_id {field_id.strip()!r} is not in this table — check feishu_bitable_list_fields."
            )
    name = field_name.strip() or str(current.get("field_name", ""))
    ftype = field_type or current.get("type", 0)
    if not name:
        return _core._error("No field_name available for this field; pass field_name explicitly.")
    if not isinstance(ftype, int) or not ftype:
        return _core._error("No field_type available for this field; pass field_type explicitly.")
    field: dict[str, Any] = {"field_name": name, "type": ftype}
    problem = _validate_bitable_fields([field], as_table_fields=False)
    if problem:
        return _core._error(
            problem.replace("fields_json[0].", "").replace("fields_json[0]", "field").replace("created", "updated")
        )
    if current.get("is_primary") and ftype not in _INDEX_FIELD_TYPES:
        return _core._error(
            f"this is the index (primary) column, so type {ftype} is not allowed; "
            f"it must be one of {sorted(_INDEX_FIELD_TYPES)} (Feishu answers 1254012)."
        )
    if is_primary is not None:
        if is_primary and ftype not in _INDEX_FIELD_TYPES:
            return _core._error(
                f"type {ftype} cannot be the index (primary) column; "
                f"it must be one of {sorted(_INDEX_FIELD_TYPES)} "
                "(1=文本, 2=数字, 5=日期, 13=电话, 15=超链接, 20=公式, 22=地理位置)."
            )
        field["is_primary"] = is_primary
    if property_json.strip():
        try:
            prop = json.loads(property_json)
        except ValueError as exc:
            return _core._error(f"property_json is not valid JSON: {exc}")
        if not isinstance(prop, dict):
            return _core._error('property_json must be a JSON object, e.g. \'{"options":[{"name":"高","color":0}]}\'.')
        field["property"] = prop
    elif current.get("property") and (field_type in (0, current.get("type"))):
        # Same type and no new property: carry the existing settings over, otherwise
        # this full-replace update would wipe the select options / number format.
        field["property"] = current["property"]
    if ui_type.strip():
        field["ui_type"] = ui_type.strip()
    res = await _core._invoke(
        _build_update_field_request(app_token.strip(), table_id.strip(), field_id.strip(), field),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    updated = data.get("field", {}) if isinstance(data.get("field"), dict) else {}
    new_type = updated.get("type", ftype)
    return {
        "ok": True,
        "field_id": updated.get("field_id", "") or field_id.strip(),
        "name": updated.get("field_name", name),
        "type": _BITABLE_FIELD_TYPES.get(new_type, new_type),
        "is_primary": bool(updated.get("is_primary") or current.get("is_primary")),
    }
