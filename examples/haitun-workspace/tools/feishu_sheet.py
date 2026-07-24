"""Feishu/Lark spreadsheet write tools — put values/formulas + set cell style.

Complements ``feishu_doc_read(file_type="sheet", ...)`` (which only *reads* a
spreadsheet). These tools *write* to a spreadsheet:

- ``feishu_sheet_write`` — overwrite a range with a grid of values/formulas.
- ``feishu_sheet_append`` — append rows after the last used row.
- ``feishu_sheet_format`` — set cell style (font/color/border/align/number-format).

Get the spreadsheet ``token`` and a worksheet's ``SHEET_ID`` from the sheet URL /
from ``feishu_docs_search``. Ranges use the ``"<SHEET_ID>!<A1:B2>"`` form; a bare
``"<SHEET_ID>"`` targets the sheet's used range.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_sheet_write(token: str, range: str, values_json: str, user_key: str = "") -> str:
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
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to write
            as that user (needed when the sheet is user-owned and the bot isn't a
            collaborator); empty uses the bot's tenant token.
    """
    return _f.dumps_result(await _f.write_sheet_impl(token, range, values_json, user_key))


async def feishu_sheet_append(
    token: str, range: str, values_json: str, insert_data_option: str = "OVERWRITE", user_key: str = ""
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
        user_key: The sender's open_id. Pass it to write as that user; empty uses tenant token.
    """
    return _f.dumps_result(await _f.append_sheet_impl(token, range, values_json, insert_data_option, user_key))


async def feishu_sheet_format(token: str, range: str, style_json: str, user_key: str = "") -> str:
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
        user_key: The sender's open_id. Pass it to write as that user; empty uses tenant token.
    """
    return _f.dumps_result(await _f.format_sheet_impl(token, range, style_json, user_key))
