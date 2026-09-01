"""Feishu/Lark spreadsheet range read + write tools.

Complements ``feishu_doc_read(file_type="sheet", ...)``, which dumps every sheet
whole. These tools target an explicit range:

- ``feishu_sheet_read`` — read a range as plain-text rows (mentions flattened).
- ``feishu_sheet_write`` — overwrite a range with a grid of values/formulas.
- ``feishu_sheet_append`` — append rows after the last used row.
- ``feishu_sheet_format`` — set cell style (font/color/border/align/number-format).

Get the spreadsheet ``token`` from the sheet URL / from ``feishu_docs_search``. Ranges
use the ``"<SHEET_ID>!<A1:B2>"`` form; a bare ``"<SHEET_ID>"`` targets the sheet's used
range. A ``SHEET_ID`` is **not** in the sheet URL — list the worksheets first via
``feishu_api(method="GET",
uri="/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query")``, see the
``feishu-api`` skill.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_sheet_read(token: str, range: str, max_chars: int = 20000, user_key: str = "") -> str:
    """Read one **narrow, already-located** range of a spreadsheet as plain-text cells.

    ⚠️ 事实问答禁用本工具:「谁的 mentor」「谁做了什么」「有几个人」「对比多少」
    这类问题必须用 ``feishu_sheet_find_columns``(定位表头列)+ ``feishu_sheet_read_grid``
    (分块读,直到 ``has_more`` 为 false)。本工具按字符预算截断(默认 20000 字符),
    富文本表格前几行就可能截断,后面的行整段丢失——用残缺数据下结论是已实测的
    高频事故。本工具只适合读一个已知的小范围(找某人的行、检查某个单元格)。

    Use this instead of ``feishu_doc_read(file_type="sheet")`` when you need a
    specific area rather than the whole workbook — e.g. scan just the name column
    to find which row a person is on, or check whether one target cell is already
    filled before overwriting it.

    **Not for reading a whole board.** This stops at ``max_chars`` and drops the
    remaining rows wholesale, returning ``truncated: true`` — on a real 列=日期、行=人
    board that is the normal outcome, not an edge case. Two rules follow:

    - **A wide range is the wrong first move.** Locate first (name column →
      person's row number; header row → target column letter), then read just that
      cell or row. To walk a whole sheet, use ``feishu_sheet_read_grid``, which pages
      with ``has_more`` / ``next_start_row`` instead of dropping rows.
    - **Never conclude from a ``truncated: true`` result.** The rows that were cut are
      absent, not empty — treating them as blank reports people as not having filled
      anything when their row was never read. Re-read narrower, or switch to
      ``feishu_sheet_read_grid``, before saying anything about who filled what.
    - **Raising ``max_chars`` is not the fix.** It is capped internally at the
      per-result limit, so a bigger number changes nothing about what comes back.
      Narrow the range or page with ``feishu_sheet_read_grid`` instead.

    Cells that are mentions (``@somebody``) or styled rich text are flattened to
    their visible text, so a name column reads as ``"张三"`` rather than raw JSON.

    Args:
        token: The spreadsheet_token (from the sheet URL, the part after ``/sheets/``).
            For a wiki-hosted sheet, convert the node token first via ``feishu_api`` on
            ``GET /open-apis/wiki/v2/spaces/get_node`` and use its ``obj_token``.
        range: Range to read, e.g. ``"SHEET_ID!A1:H30"`` or just ``"SHEET_ID"``
            for the sheet's used range.
        max_chars: Stop after roughly this many characters of cell text. Capped
            internally at the per-result limit, and ``0`` means "that cap" rather
            than "unlimited" — **raising this above the cap does nothing**, since a
            result over the limit is cut on the wire regardless. The effective value
            is echoed as ``max_chars_effective`` when a read is truncated. To get
            more data, narrow the range or page with ``feishu_sheet_read_grid``.
        user_key: The sender's open_id (from ``<feishu_context>``). Reads try the bot's
            tenant token first and only fall back to this user's identity when the bot
            is denied — pass it whenever the sheet may be user-owned.
    """
    outcome = await _f.read_sheet_range_impl(token, range, max_chars, user_key)
    if outcome.get("ok"):
        # 列字母表头 + 行号首列内嵌:对齐由数据自证,LLM 不用手数。
        outcome = _f._label_grid(outcome)
    return _f.dumps_result(outcome)


async def feishu_sheet_write(token: str, range: str, values_json: str, user_key: str = "", identity: str = "") -> str:
    """Write (overwrite) a grid of values or formulas into a spreadsheet range.

    Existing cells in the range are overwritten. A cell whose value is a string
    beginning with ``=`` (e.g. ``"=SUM(A1:A2)"``) is stored as a formula. Cells
    may be string / number / bool / null (null = blank). The range must be at
    least as large as the grid. Single-write cap: 5000 rows x 100 columns.

    Args:
        token: The spreadsheet_token (from the sheet URL, the part after ``/sheets/``).
        range: Target range, e.g. ``"SHEET_ID!A1:C3"`` or just ``"SHEET_ID"``.
        values_json: A JSON array of rows, each row a JSON array of cells —
            e.g. ``'[["Name","Score"],["Alice",95],["Total","=SUM(B2:B2)"]]'``.
        user_key: The sender's open_id (from ``<feishu_context>``). A user-owned sheet
            generally needs that user's identity, since the bot isn't a collaborator.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.write_sheet_impl(token, range, values_json, user_key, identity))


async def feishu_sheet_append(
    token: str,
    range: str,
    values_json: str,
    insert_data_option: str = "OVERWRITE",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append rows of values/formulas after the last used row of a spreadsheet range.

    Unlike ``feishu_sheet_write`` (which overwrites a fixed range), this finds the
    end of the data within ``range`` and appends below it. Same cell rules apply
    (``=...`` strings become formulas; null = blank).

    Args:
        token: The spreadsheet_token (from the sheet URL).
        range: Range to search for the append point, e.g. ``"SHEET_ID!A1:C1"`` or ``"SHEET_ID"``.
        values_json: A JSON array of rows (list of lists) to append.
        insert_data_option: ``"OVERWRITE"`` (default; overwrite following rows if not
            enough blank rows) or ``"INSERT_ROWS"`` (insert new rows first).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(
        await _f.append_sheet_impl(token, range, values_json, insert_data_option, user_key, identity)
    )


async def feishu_sheet_format(token: str, range: str, style_json: str, user_key: str = "", identity: str = "") -> str:
    """Apply a cell style (font, color, border, alignment, number format) to a range.

    ``style_json`` is a JSON object of Feishu style fields, e.g.::

        {"font": {"bold": true, "fontSize": "10pt/1.5"},
         "foreColor": "#000000", "backColor": "#21d11f",
         "hAlign": 1, "vAlign": 1, "borderType": "FULL_BORDER",
         "borderColor": "#ff0000", "textDecoration": 0, "formatter": ""}

    Fields: ``font.{bold,italic,fontSize,clean}``, ``textDecoration`` (0 none/1
    underline/2 strikethrough/3 both), ``formatter`` (number format), ``hAlign``
    (0 left/1 center/2 right), ``vAlign`` (0 top/1 middle/2 bottom), ``foreColor``,
    ``backColor``, ``borderType`` (FULL_BORDER/OUTER_BORDER/…/NO_BORDER),
    ``borderColor``, ``clean`` (clear all formatting). Cap: 5000 rows x 100 cols
    (border updates ≤ 30000 cells) per call.

    Args:
        token: The spreadsheet_token (from the sheet URL).
        range: Target range, e.g. ``"SHEET_ID!A1:C3"``.
        style_json: A JSON object of the style fields to apply.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.format_sheet_impl(token, range, style_json, user_key, identity))
