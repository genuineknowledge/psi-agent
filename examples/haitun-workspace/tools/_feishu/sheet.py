"""Feishu Sheets — read ranges, write/append values, cell styles.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import datetime
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
    return {
        "ok": True,
        "token": token.strip(),
        "range": value_range.get("range", range_.strip()),
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


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


# ── Structured grid reads (读取可靠性 P0:显式分块 + 确定性列定位) ──────────────
#
# 背景:整表读取有 20000 字符预算,大表被静默截断,海豚拿残缺数据下结论
# (已定位的案例 1/3)。这两个函数把「读到哪、还有没有」变成显式事实:
# 分块读取逐块报告行范围,列语义由代码判定而不是模型数表头。


def _col_letter(col: int) -> str:
    """1 → A, 26 → Z, 27 → AA …(Excel 列号转字母)"""
    out = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


async def _first_sheet_id(token: str, user_key: str) -> tuple[str, str]:
    """Resolve the first worksheet's id (and title) of a spreadsheet."""
    meta = await _core._invoke(_build_sheet_meta_request(token), user_key=user_key)
    if not meta.get("ok"):
        raise RuntimeError(meta.get("error") or meta.get("message") or "sheet meta query failed")
    sheets = meta.get("data", {}).get("sheets", []) if isinstance(meta.get("data"), dict) else []
    for sh in sheets:
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        if sheet_id:
            return str(sheet_id), str(sh.get("title") or "")
    raise RuntimeError("spreadsheet has no worksheet")


async def read_sheet_grid_impl(
    token: str, range_: str = "", max_rows: int = 50, start_row: int = 1, user_key: str = ""
) -> dict[str, Any]:
    """Read a spreadsheet in row blocks with explicit coordinates and progress.

    Never truncates silently: ``has_more`` / ``next_start_row`` tell the caller
    exactly where the read stopped, and the caller must continue from
    ``next_start_row`` until ``has_more`` is false. ``range_`` may pin one
    worksheet (``<sheetId>`` or ``<sheetId>!A1:B30``); empty means the first
    worksheet. Row numbers are 1-based and align with the sheet's own rows.
    """
    if not token.strip():
        return _core._error("token (spreadsheet_token) is required.")
    if max_rows < 1:
        return _core._error("max_rows must be >= 1")
    if start_row < 1:
        return _core._error("start_row must be >= 1 (sheet rows are 1-based)")
    sheet_id = ""
    try:
        if range_ and range_.strip():
            head = range_.strip().split("!")[0]
            if head:
                sheet_id = head
        if not sheet_id:
            sheet_id, _ = await _first_sheet_id(token, user_key)
    except RuntimeError as e:
        return _core._error(str(e))
    block_range = f"{sheet_id}!A{start_row}:ZZ{start_row + max_rows - 1}"
    res = await _core._invoke(_build_sheet_values_request(token, block_range), user_key=user_key)
    if not res.get("ok"):
        return res
    value_range = res.get("data", {}).get("valueRange", {}) if isinstance(res.get("data"), dict) else {}
    raw_rows = value_range.get("values") or []
    rows = [[_flatten_sheet_cell(c) for c in (raw_row if isinstance(raw_row, list) else [])] for raw_row in raw_rows]
    has_more = len(rows) >= max_rows
    return {
        "ok": True,
        "sheet": sheet_id,
        "range": value_range.get("range", block_range),
        "start_row": start_row,
        "row_count": len(rows),
        "has_more": has_more,
        "next_start_row": start_row + len(rows) if has_more else None,
        "truncated": has_more,
        "rows": rows,
    }


_DATE_PATTERNS = [
    (r"^\d{1,2}\.\d{1,2}(?:日)?$", "%m.%d"),  # 7.24 / 8.10日
    (r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", "%Y-%m-%d"),  # 2026-08-14
    (r"^\d{1,2}[-/]\d{1,2}$", "%m-%d"),  # 8/14
]
_HEADER_KIND_RULES = [
    ("mentor", "mentor"),
    ("负责人", "names"),
    ("姓名", "names"),
    ("名字", "names"),
    ("owner", "names"),
]


def _classify_header(header: str) -> tuple[str, str]:
    """Classify one header cell into (kind, normalized_date)."""
    text = header.strip()

    for pattern, fmt in _DATE_PATTERNS:
        m = re.fullmatch(pattern, text)
        if m:
            try:
                normalized = datetime.datetime.strptime(m.group(0).rstrip("日"), fmt)
            except ValueError:
                normalized = datetime.datetime(1900, 1, 1)
            # 无年份的日期列(如 8.14)按当前年份归一,不给 1900 这种误导值。
            if normalized.year == 1900:
                normalized = normalized.replace(year=datetime.datetime.now().year)
            return "date", normalized.strftime("%Y-%m-%d")
    low = text.lower()
    for marker, kind in _HEADER_KIND_RULES:
        if marker in low:
            return kind, ""
    return "other", ""


async def find_sheet_columns_impl(
    token: str, header_row: int = 1, range_: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Read the header row and classify every column's semantics in code.

    Date columns (7.24 / 8.10日 / 2026-08-14 …) are recognized by pattern and
    normalized to ISO dates; name columns (负责人/姓名/名字) and the mentor
    column are recognized by markers. The agent should use this to locate
    columns instead of counting header cells by eye — the counted-column
    mistakes (案例 3 的行列横跳) come from exactly that.
    """
    if not token.strip():
        return _core._error("token (spreadsheet_token) is required.")
    if header_row < 1:
        return _core._error("header_row must be >= 1")
    sheet_id = ""
    try:
        if range_ and range_.strip():
            head = range_.strip().split("!")[0]
            if head:
                sheet_id = head
        if not sheet_id:
            sheet_id, _ = await _first_sheet_id(token, user_key)
    except RuntimeError as e:
        return _core._error(str(e))
    header_range = f"{sheet_id}!A{header_row}:ZZ{header_row}"
    res = await _core._invoke(_build_sheet_values_request(token, header_range), user_key=user_key)
    if not res.get("ok"):
        return res
    value_range = res.get("data", {}).get("valueRange", {}) if isinstance(res.get("data"), dict) else {}
    raw_rows = value_range.get("values") or []
    cells = raw_rows[0] if raw_rows and isinstance(raw_rows, list) else []
    # 返回 range 形如 "sheetId!A1:E1":起始列从第 6 个字符开始解析。
    start_col = 1
    range_text = str(value_range.get("range", ""))
    m = re.search(r"!([A-Z]+)\d+", range_text)
    if m:
        col_letters = m.group(1)
        start_col = 0
        for ch in col_letters:
            start_col = start_col * 26 + (ord(ch) - ord("A") + 1)
    columns: list[dict[str, Any]] = []
    for offset, cell in enumerate(cells):
        text = _flatten_sheet_cell(cell)
        if not text:
            continue
        kind, normalized = _classify_header(text)
        columns.append(
            {
                "col": _col_letter(start_col + offset),
                "header": text,
                "kind": kind,
                **({"date": normalized} if kind == "date" else {}),
            }
        )
    return {
        "ok": True,
        "sheet": sheet_id,
        "header_row": header_row,
        "columns": columns,
    }
