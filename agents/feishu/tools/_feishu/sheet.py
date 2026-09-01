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

from psi_agent.session.history_display import MAX_TOOL_RESULT_CHARS


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
    # 飞书对单格区间要求 A1:A1 形式:裸 "A1" 直接报 90202 wrong range。
    # 报错信息若被调用方当作「读到了空单元格」,就会把有内容的格子误判成空
    # (2026-08-26 实测:J31 报 90202 → 海豚下结论「8.7 没写」,实际 J31 有内容)。
    # 这里静默补全成单格区间,消灭这类误判。
    range_value = range_.strip()
    if "!" in range_value:
        sheet_part, _, cell_part = range_value.rpartition("!")
        if cell_part and ":" not in cell_part:
            range_value = f"{sheet_part}!{cell_part}:{cell_part}"
    else:
        # 不带 sheetId 前缀的 range(如 "B25:S25")飞书可能返回空或含糊错误,
        # 调用方会误读成「数据为空」(2026-08-26 实测:海豚排查数轮才发现
        # 缺前缀)。显式报错,把「怎么修」直接写进错误信息。
        return _core._error(
            f"range {range_value!r} 缺少工作表前缀 — 必须写成 '<sheetId>!{range_value}' 形式;"
            " sheetId 用 GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query 查询。"
        )
    res = await _core._invoke(_build_sheet_values_request(token.strip(), range_value), user_key=user_key)
    if not res["ok"]:
        return res
    value_range = res["data"].get("valueRange", {}) if isinstance(res["data"], dict) else {}
    raw_rows = value_range.get("values") or []
    rows: list[list[str]] = []
    truncated = False
    # 调用方的预算再大也越不过会话层 20000 字符硬切,而那一刀落在 JSON 中间会把
    # 整个结果切成非法 JSON(连 truncated 警告本身都读不到)。实测调用方看到截断后
    # 反手把 max_chars 提到 80000,结果照旧被切到 20053 —— 「调大预算」反而稳定
    # 制造坏 JSON。所以这里向下收敛,让本函数的 truncated+warning 一定能被看见。
    # max_chars=0(不限)同样受此上限约束:上限之外没有「不限」可言。
    effective_max_chars = min(max_chars, _GRID_TEXT_BUDGET) if max_chars > 0 else _GRID_TEXT_BUDGET
    budget = effective_max_chars
    for raw_row in raw_rows if isinstance(raw_rows, list) else []:
        cells = [_flatten_sheet_cell(c) for c in (raw_row if isinstance(raw_row, list) else [])]
        spent = sum(len(c) for c in cells)
        # 首行永远给:一行就超预算时也得让调用方看见内容而不是空网格 + 一句警告。
        if rows and spent > budget:
            truncated = True
            break
        budget -= spent
        rows.append(cells)
    outcome: dict[str, Any] = {
        "ok": True,
        "token": token.strip(),
        "range": value_range.get("range", range_.strip()),
        "cols": _cols_from_range(value_range.get("range", range_.strip()), _data_width(rows)),
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
    if truncated:
        # A bare ``truncated: true`` reads as a detail next to a plausible-looking grid,
        # and the rows that were cut are *absent* rather than empty — so a caller that
        # answers anyway reports people as having filled nothing when their row was never
        # fetched. Say what is missing and what to do instead, in the payload itself.
        outcome["rows_dropped_after_row"] = len(rows)
        # 报生效预算而不是入参:入参可能被上限收敛掉,照着入参说「Truncated at 80000
        # chars」会让调用方以为再调大就能读全,而那条路是不存在的。
        outcome["max_chars_effective"] = effective_max_chars
        outcome["warning"] = (
            f"Truncated at {effective_max_chars} chars: only the first {len(rows)} row(s) of this range are "
            "present and the rest were dropped, NOT read as empty. Do not draw conclusions about "
            "who filled what from this result. Raising max_chars will NOT help — this is the hard "
            "per-result cap. Either narrow the range (locate the person's row first, then read that "
            "row/cell) or page the sheet with feishu_sheet_read_grid, which reports has_more / "
            "next_start_row instead of dropping rows."
        )
    return outcome


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


def _col_index(letters: str) -> int:
    """A → 1, Z → 26, AA → 27 …(Excel 列字母转列号,1-based)"""
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _cols_from_range(range_str: str, width: int) -> list[str]:
    """Derive the per-cell column letters from a valueRange's range string.

    模型对齐列是已实测的高频错误(把 A 当 B、手数表头偏一列全盘错)。返回里带上
    与每行 cell 一一对应的列字母数组,对齐变成直接索引,不再依赖模型推理。
    """
    try:
        cell_range = range_str.split("!", 1)[1].split(":", 1)[0]
        start = _col_index("".join(c for c in cell_range if c.isalpha()))
    except Exception:
        start = 1
    return [_col_letter(start + i) for i in range(max(width, 0))]


def _data_width(rows: list[list[str]]) -> int:
    """Width to emit ``cols`` for — up to the last non-empty cell, no trailing noise."""
    width = 0
    for row in rows:
        for i, cell in enumerate(row):
            if cell:
                width = max(width, i + 1)
    return width


# 留给 JSON 骨架的余量:一格文本在结果里不止它本身 —— 引号/逗号约 4 字符,
# 加上 _label_grid 补的行号列和 filled_cols(每行一份列字母清单)。按 0.7 折算,
# 保证「本工具自报的 has_more」先生效,而不是让会话层的硬切先把 JSON 切烂。
_GRID_TEXT_BUDGET = int(MAX_TOOL_RESULT_CHARS * 0.7)


def _fit_rows_to_budget(rows: list[list[str]], budget: int = _GRID_TEXT_BUDGET) -> tuple[list[list[str]], bool]:
    """Drop whole trailing rows until the block's cell text fits ``budget``.

    Returns the kept rows and whether anything was dropped. Cuts only at row
    boundaries: a half-row would be indistinguishable from a row whose later
    columns are empty, which is exactly the misread this module exists to stop.

    Always keeps at least one row, even when that row alone busts the budget —
    otherwise ``next_start_row`` would equal ``start_row`` and a caller paging
    until ``has_more`` is false would spin on the same block forever.
    """
    kept: list[list[str]] = []
    spent = 0
    for row in rows:
        cost = sum(len(cell) for cell in row)
        if kept and spent + cost > budget:
            return kept, True
        kept.append(row)
        spent += cost
    return kept, len(kept) < len(rows)


def _start_row_from_range(range_str: str) -> int:
    """Parse the first row number out of a valueRange range like ``46a582!R2:R41``."""
    try:
        cell = range_str.split("!", 1)[1].split(":", 1)[0]
        return int("".join(c for c in cell if c.isdigit()))
    except Exception:
        return 1


def _col_span_from_range(range_str: str) -> tuple[str, str]:
    """Extract the column letters a caller pinned in ``range_``, e.g. ``S!B1:O80`` → ``("B", "O")``.

    Returns ``("", "")`` when the range names no worksheet-relative columns (a bare
    ``<sheetId>``, or an unparseable range) — the caller then falls back to the full
    ``A:ZZ`` span. Only the letters are taken: row numbers come from ``start_row`` /
    ``max_rows`` so paging stays under the caller's control.
    """
    if "!" not in range_str:
        return "", ""
    cells = range_str.split("!", 1)[1]
    start, _, end = cells.partition(":")
    first = "".join(c for c in start if c.isalpha()).upper()
    last = "".join(c for c in end if c.isalpha()).upper() or first
    if not first:
        return "", ""
    # 反着写的区间(O1:B80)按小→大归一,否则飞书报 wrong range 而调用方只看到一句报错。
    if _col_index(last) < _col_index(first):
        first, last = last, first
    return first, last


def _label_grid(outcome: dict[str, Any]) -> dict[str, Any]:
    """Embed a column-letter header row and a row-number first column into ``rows``.

    对齐由数据自证:表头行写列字母,每行行首写真实行号。已实测两类事故 ——
    列对齐手数偏一列(8.17 被当成 8.14)、行对齐分次读取后截断错位(没写的人
    被报成写了)。LLM 无论怎么数,标签就在数据里,不再依赖推理。
    """
    rows = outcome.get("rows")
    if not isinstance(rows, list):
        return outcome
    start = int(outcome.get("start_row") or 0) or _start_row_from_range(str(outcome.get("range", "")))
    header: list[str] = ["行"]
    cols = outcome.get("cols")
    if isinstance(cols, list):
        header += [str(c) for c in cols]
    width = len(cols) if isinstance(cols, list) else 0
    labeled: list[list[str]] = [header]
    filled: dict[str, list[str]] = {}
    for i, row in enumerate(rows):
        if isinstance(row, list):
            # 行只留到 cols 宽度:飞书按列宽填满空串尾(如 A36:ZZ36 读回 700 格),
            # 长尾空串淹没结构,数索引数错(实测:空列被相邻文本里的日期带偏)。
            cells = [str(c) for c in row[:width]]
            labeled.append([str(start + i), *cells])
            if isinstance(cols, list):
                # 每行非空列字母清单,由代码判定——「某人某天是否写过」直接查它,
                # 别从单元格文本里的日期数字推断(实测:8.21 格内容提到 (8.24),
                # 被当成 8.24 列写了,漏写的人被报成没漏)。
                filled[str(start + i)] = [str(cols[j]) for j, cell in enumerate(cells) if cell]
        else:
            labeled.append([str(start + i)])
    outcome["rows"] = labeled
    outcome["filled_cols"] = filled
    if len(rows) == 1 and isinstance(rows[0], list) and isinstance(cols, list) and cols:
        # 单行读取附 cells 映射(列字母键 → 内容):取某列内容按键查,
        # 别从 rows 数组数第几个元素——超长文本连排时数错一格即偏一格
        # (实测:报 R 列内容读成了 Q 列的)。多行读取不附(体积爆炸),取内容
        # 改用单行/单格读取。
        only = [str(c) for c in rows[0][:width]]
        outcome["cells"] = {str(start): {str(cols[j]): cell for j, cell in enumerate(only) if cell}}
    return outcome


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
    worksheet (``<sheetId>``) and optionally the **columns** to fetch
    (``<sheetId>!B1:O80`` reads only B..O) — row numbers in ``range_`` are
    ignored, since paging is driven by ``start_row`` / ``max_rows``. Empty means
    the first worksheet, full ``A:ZZ`` width. Row numbers are 1-based and align
    with the sheet's own rows.

    The block is additionally capped at ``MAX_TOOL_RESULT_CHARS`` worth of cell
    text, cutting **at a row boundary** and folding the cut into
    ``has_more`` / ``next_start_row``, so a caller that keeps paging still sees
    every row.
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
    # 列范围曾被整段丢掉(只取 sheetId,区间恒为 A:ZZ):调用方「改用窄范围读人名列」
    # 是哑操作,读回来的还是整表全宽。实测一次 B1:B80 请求拉回 322338 字符,把
    # 会话层 20000 上限撑爆——正是本工具存在的意义被反过来抵消。
    first_col, last_col = _col_span_from_range(range_.strip() if range_ else "")
    first_col = first_col or "A"
    last_col = last_col or "ZZ"
    block_range = f"{sheet_id}!{first_col}{start_row}:{last_col}{start_row + max_rows - 1}"
    res = await _core._invoke(_build_sheet_values_request(token, block_range), user_key=user_key)
    if not res.get("ok"):
        return res
    value_range = res.get("data", {}).get("valueRange", {}) if isinstance(res.get("data"), dict) else {}
    raw_rows = value_range.get("values") or []
    rows = [[_flatten_sheet_cell(c) for c in (raw_row if isinstance(raw_row, list) else [])] for raw_row in raw_rows]
    # 行边界字符预算:会话层 truncate_tool_result 会在 20000 字符处硬切,切口落在
    # JSON 中间 → 整个结果不再是合法 JSON,has_more/next_start_row 一起烂掉,
    # 「读到 has_more 为 false」的契约当场失效(实测四次 read_grid 返回全部
    # json.loads 失败)。所以宁可自己按整行少给几行,让分页元数据始终可信。
    rows, budget_cut = _fit_rows_to_budget(rows)
    # 没有 truncated 字段:行数据完整(要么整行给要么不给),字符预算的后果全部
    # 折进 has_more/next_start_row。曾经把 truncated 直接映射成 has_more,单行读取
    # (max_rows=1)恒报 truncated=true,被当成「截断警告不适用」而整体无视。
    has_more = budget_cut or len(rows) >= max_rows
    return {
        "ok": True,
        "sheet": sheet_id,
        "range": value_range.get("range", block_range),
        "cols": _cols_from_range(value_range.get("range", block_range), _data_width(rows)),
        "start_row": start_row,
        "row_count": len(rows),
        "has_more": has_more,
        "next_start_row": start_row + len(rows) if has_more else None,
        "rows": rows,
    }


#: 日期表头的写法。年份可有可无,分隔符 . - / 通吃,允许尾巴带「日」。
_DATE_PATTERNS = [
    re.compile(r"^(?:(?P<year>\d{4})[.\-/])?(?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})日?$"),
]


def _header_date(header: str) -> str:
    """Return the ISO date a header cell denotes, or "" if it is not a date.

    Only dates are recognized here. Headers used to be classified further —
    ``负责人``/``姓名``/``owner`` became ``names``, anything containing
    ``mentor`` became ``mentor``, everything else ``other`` — off a hardcoded
    substring table. That table decided meaning it could not know:

    * a Chinese mentor column (导师 / 带教 / 师父) matched nothing and came back
      ``other``, which reads as "there is no mentor column here" and sends the
      model back to counting header cells — the exact mistake this lookup was
      added to prevent;
    * a header naming two roles (``带教负责人(mentor)``) was decided by the
      table's write order, not by the header, so 负责人 and mentor came back
      swapped.

    Reading a header is the model's job; it has the whole sheet and the question
    in front of it. What it cannot do by eye is column arithmetic, so that is
    what stays in code: the column letter, and this normalization.

    数字直接从分组里取,不走 ``strptime``:原先按「模式 → 格式串」配对解析,``7/24``
    配到的格式串是 ``%m-%d``,分隔符对不上就抛 ValueError,再被兜底成 1900-01-01、
    补年份变成「今年 1 月 1 日」—— 一个错得看不出来的日期。``2.29`` 同理(strptime
    的默认年份 1900 不是闰年)。
    """
    text = header.strip()
    for pattern in _DATE_PATTERNS:
        m = pattern.fullmatch(text)
        if not m:
            continue
        # 无年份的日期列(如 8.14)按当前年份归一,不给 1900 这种误导值。
        year = int(m.group("year") or datetime.datetime.now().year)
        try:
            return datetime.date(year, int(m.group("month")), int(m.group("day"))).isoformat()
        except ValueError:
            # 13.32 这种「像日期但不是日期」的表头:当普通表头处理,不编一个日期出来。
            return ""
    return ""


async def find_sheet_columns_impl(
    token: str, header_row: int = 1, range_: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Read the header row and return each non-empty header with its column letter.

    Headers come back verbatim — reading what a column means is left to the
    caller (see ``_header_date`` for why). Two things are computed here because
    they are arithmetic, not judgement: the column letter (26-base, offset by
    where the returned range actually starts, so a ``!B1`` range does not report
    its first cell as A) and, for cycle columns like 7.24 / 8.10日 /
    2026-08-14, the normalized ISO ``date`` (``kind: "date"``).
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
        normalized = _header_date(text)
        columns.append(
            {
                "col": _col_letter(start_col + offset),
                "header": text,
                # kind 只在日期列出现:那是归一算出来的结论。其他列不带 kind —— 与其
                # 给个 "other" 让人当成「查过了,没别的意思」,不如什么都不说。
                **({"kind": "date", "date": normalized} if normalized else {}),
            }
        )
    return {
        "ok": True,
        "sheet": sheet_id,
        "header_row": header_row,
        "columns": columns,
    }
