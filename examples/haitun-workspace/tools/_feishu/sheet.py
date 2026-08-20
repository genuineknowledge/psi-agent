"""Feishu Sheets — read ranges, write/append values, cell styles.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import json
import re
from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest


def _build_sheet_meta_request(spreadsheet_token: str) -> BaseRequest:
    return _core._raw_get(
        "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "spreadsheet_token",
        spreadsheet_token,
    )


def _build_sheet_values_request(spreadsheet_token: str, range_: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.paths["range"] = range_
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _sheet_values_to_text(data: dict[str, Any]) -> str:
    grid = data.get("valueRange", {}).get("values", []) if isinstance(data, dict) else []
    lines: list[str] = []
    for row in grid if isinstance(grid, list) else []:
        cells = [("" if c is None else str(c)) for c in (row if isinstance(row, list) else [])]
        lines.append("\t".join(cells))
    return "\n".join(lines)


def _flatten_sheet_cell(cell: Any) -> str:
    """Flatten one Feishu sheet cell into plain text.

    A cell is not always a scalar: mention cells (``@somebody``) arrive as a dict
    with ``type="mention"``, and styled cells arrive as a list of run segments
    (``{"type": "text", "text": ..., "segmentStyle": ...}``). Reading the "人名"
    or "mentor" column of a todo board therefore needs this flattening, otherwise
    the name is buried in JSON.
    """
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    if isinstance(cell, str):
        return cell
    if isinstance(cell, (int, float)):
        return str(cell)
    if isinstance(cell, list):
        return "".join(_flatten_sheet_cell(part) for part in cell)
    if isinstance(cell, dict):
        for key in ("text", "name", "en_name", "link"):
            val = cell.get(key)
            if isinstance(val, str) and val:
                return val
        return ""
    return str(cell)


_RANGE_RE = re.compile(r"^(?P<sheet>[^!]+)!(?P<c1>[A-Za-z]+)(?P<r1>\d+)(?::(?P<c2>[A-Za-z]+)(?P<r2>\d+))?$")


def _next_range_after(full_range: str, rows_read: int) -> str | None:
    """Unread portion after a truncating read, e.g. ``46a582!A1:Z198`` + 8 rows → ``46a582!A9:Z198``.

    The API's ``valueRange.range`` is always full A1 notation; a bare sheet id
    (unit-test mocks use ``"S1"``) counts as starting at row 1. Unparseable
    ranges return ``None`` — the caller then falls back to a generic note.
    """
    m = _RANGE_RE.match(full_range or "")
    if not m:
        if full_range and "!" not in full_range and rows_read > 0:
            return f"{full_range}!A{1 + rows_read}"
        return None
    sheet, c1, r1 = m.group("sheet"), m.group("c1"), int(m.group("r1"))
    if m.group("r2") is None:
        return f"{sheet}!{c1}{r1 + rows_read}"
    if r1 + rows_read > int(m.group("r2")):
        return None
    return f"{sheet}!{c1}{r1 + rows_read}:{m.group('c2')}{m.group('r2')}"


async def read_sheet_range_impl(token: str, range_: str, max_chars: int = 20000, user_key: str = "") -> dict[str, Any]:
    """Read one explicit range of a spreadsheet as a grid of plain-text cells.

    Complements ``read_doc_impl(file_type="sheet")``, which dumps *every* sheet
    whole. Reading an explicit range is what lets a caller (a) locate a person's
    row by scanning just the name column and (b) check whether one target cell is
    already occupied before overwriting it.
    """
    if not token.strip():
        return _core._error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _core._error("range is required, e.g. 'SHEET_ID!A1:H30' or just 'SHEET_ID'.")
    res = await _core._invoke(_build_sheet_values_request(token.strip(), range_.strip()), user_key=user_key)
    if not res["ok"]:
        return res
    value_range = res["data"].get("valueRange", {}) if isinstance(res["data"], dict) else {}
    raw_rows = value_range.get("values") or []
    rows: list[list[str]] = []
    truncated = False
    budget = max_chars
    for raw_row in raw_rows if isinstance(raw_rows, list) else []:
        cells = [_flatten_sheet_cell(c) for c in (raw_row if isinstance(raw_row, list) else [])]
        if max_chars > 0:
            spent = sum(len(c) for c in cells)
            if spent > budget:
                truncated = True
                break
            budget -= spent
        rows.append(cells)
    result = {
        "ok": True,
        "token": token.strip(),
        "range": value_range.get("range", range_.strip()),
        "rows": rows,
        "row_count": len(rows),  # legacy name (tests assert it) = rows returned this call
        "rows_read": len(rows),
        "rows_total": len(raw_rows),
        "truncated": truncated,
    }
    if truncated:
        result["next_range"] = _next_range_after(result["range"], len(rows))
        if result["next_range"]:
            where = f"请用 range='{result['next_range']}' 继续调用 feishu_sheet_read 读剩余部分"
        else:
            where = "请用更小的行区间(如逐 10 行)继续读取"
        result["note"] = (
            f"⚠️ 内容超过 max_chars={max_chars},本次只返回前 {len(rows)}/{len(raw_rows)} 行。"
            f"{where},直到 truncated=false。row_count/rows_read 是已返回行数,不是表格总行数。"
        )
    return result


async def _read_sheet(token: str) -> dict[str, Any]:
    meta = await _core._invoke(_build_sheet_meta_request(token))
    if not meta["ok"]:
        return meta
    sheets = meta["data"].get("sheets", [])
    parts: list[str] = []
    for sh in sheets if isinstance(sheets, list) else []:
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        title = sh.get("title", "")
        if not sheet_id:
            continue
        values = await _core._invoke(_build_sheet_values_request(token, str(sheet_id)))
        if not values["ok"]:
            return values
        parts.append(f"# {title}\n{_sheet_values_to_text(values['data'])}")
    return {"ok": True, "content": "\n\n".join(parts)}


# ── Sheet writes — put/append values (incl. formulas) + set cell style ─────────
# Feishu Sheets v2 write APIs. A cell value that is a string starting with "="
# (e.g. "=SUM(A1:A2)") is stored by Feishu as a formula, so callers can write
# formulas simply by passing such strings. Ranges use the "<sheetId>!<A1:B2>"
# form; a bare "<sheetId>" targets the used range. Values may be str/int/float/
# bool/None (None = blank cell). See feishu_sheet.py for the user-facing tools.

# Feishu single-write cap: 5000 rows x 100 cols. We surface a clear error rather
# than letting the API reject a too-large payload with an opaque code.
_SHEET_MAX_ROWS = 5000
_SHEET_MAX_COLS = 100

# JSON-serialisable cell scalars Feishu accepts in a values grid.
_SHEET_CELL_TYPES = (str, int, float, bool)


def _validate_sheet_values(values: Any) -> str | None:
    """Return an error message if ``values`` isn't a valid grid, else None."""
    if not isinstance(values, list) or not values:
        return "values must be a non-empty list of rows (list of lists)."
    if not all(isinstance(row, list) for row in values):
        return "values must be a list of lists — each row is a list of cells."
    if len(values) > _SHEET_MAX_ROWS:
        return f"too many rows ({len(values)} > {_SHEET_MAX_ROWS} per write)."
    for row in values:
        if len(row) > _SHEET_MAX_COLS:
            return f"too many columns ({len(row)} > {_SHEET_MAX_COLS} per write)."
        for cell in row:
            if cell is not None and not isinstance(cell, _SHEET_CELL_TYPES):
                return f"unsupported cell value {cell!r} — use string/number/bool/null."
    return None


def _build_sheet_write_request(spreadsheet_token: str, range_: str, values: list[list[Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"valueRange": {"range": range_, "values": values}}
    return req


def _build_sheet_append_request(
    spreadsheet_token: str, range_: str, values: list[list[Any]], insert_data_option: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.add_query("insertDataOption", insert_data_option)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"valueRange": {"range": range_, "values": values}}
    return req


def _build_sheet_style_request(spreadsheet_token: str, range_: str, style: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"appendStyle": {"range": range_, "style": style}}
    return req


def _sheet_result(res: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Feishu sheet write response into the tool's success shape."""
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "spreadsheet_token": data.get("spreadsheetToken", ""),
        "updated_range": data.get("updatedRange") or data.get("tableRange", ""),
        "updated_rows": data.get("updatedRows"),
        "updated_columns": data.get("updatedColumns"),
        "updated_cells": data.get("updatedCells"),
        "revision": data.get("revision"),
    }


def _parse_values_json(values_json: str) -> tuple[list[list[Any]] | None, str | None]:
    """Parse a JSON grid string; return (values, error_message)."""
    try:
        values = json.loads(values_json)
    except ValueError as exc:
        return None, f"values_json is not valid JSON: {exc}"
    err = _validate_sheet_values(values)
    if err:
        return None, err
    return values, None


async def write_sheet_impl(
    token: str,
    range_: str,
    values_json: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Overwrite the given range of a spreadsheet with a grid of values/formulas."""
    if not token.strip():
        return _core._error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _core._error("range is required, e.g. 'SHEET_ID!A1:C3' or just 'SHEET_ID'.")
    values, err = _parse_values_json(values_json)
    if err or values is None:
        return _core._error(err or "values_json produced no rows.")
    res = await _core._invoke(
        _build_sheet_write_request(token.strip(), range_.strip(), values),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    return _sheet_result(res)


async def append_sheet_impl(
    token: str,
    range_: str,
    values_json: str,
    insert_data_option: str = "OVERWRITE",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Append rows after the last used row of the given range."""
    if not token.strip():
        return _core._error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _core._error("range is required, e.g. 'SHEET_ID!A1:C3' or just 'SHEET_ID'.")
    option = insert_data_option.strip().upper() or "OVERWRITE"
    if option not in ("OVERWRITE", "INSERT_ROWS"):
        return _core._error("insert_data_option must be 'OVERWRITE' or 'INSERT_ROWS'.")
    values, err = _parse_values_json(values_json)
    if err or values is None:
        return _core._error(err or "values_json produced no rows.")
    res = await _core._invoke(
        _build_sheet_append_request(token.strip(), range_.strip(), values, option),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    return _sheet_result(res)


async def format_sheet_impl(
    token: str,
    range_: str,
    style_json: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Apply a cell style (font/color/border/alignment/number-format) to a range."""
    if not token.strip():
        return _core._error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _core._error("range is required, e.g. 'SHEET_ID!A1:C3'.")
    try:
        style = json.loads(style_json)
    except ValueError as exc:
        return _core._error(f"style_json is not valid JSON: {exc}")
    if not isinstance(style, dict) or not style:
        return _core._error("style_json must be a non-empty JSON object of style fields.")
    res = await _core._invoke(
        _build_sheet_style_request(token.strip(), range_.strip(), style),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    return _sheet_result(res)
