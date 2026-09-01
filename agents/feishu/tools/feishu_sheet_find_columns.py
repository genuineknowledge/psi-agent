"""Read a sheet's header row and return every column's letter and header text.

Counting header cells by eye is a proven failure mode (columns misidentified,
person rows read as empty), and the arithmetic is easy to get wrong: column
letters are 26-base (column 27 is AA) and a range that starts at B must not
report its first cell as A. That is what this tool does for you.

Each non-empty header comes back as ``{"col": "C", "header": "导师"}`` — the
header text **verbatim**, in sheet order. Reading what a column means is your
job, not the tool's: you have the question and the whole header row. The one
exception is dates, which need normalizing rather than interpreting — cycle
columns like 7.24 / 8.10日 / 2026-08-14 also carry ``kind: "date"`` and an ISO
``date``. No other ``kind`` is returned; a column without one simply was not a
date, which says nothing about its meaning.

Two headers can name different roles while sharing words (``负责人`` vs
``带教负责人``): compare the full header text, and if two columns are equally
plausible, read a few data rows from each before deciding rather than guessing.

Use it before any fact question: resolve the column letters, then read the
needed columns/rows with ``feishu_sheet_read_grid`` (or ``feishu_sheet_read``
for a narrow range). Never locate columns by counting from memory.

Args:
    token: The spreadsheet_token (from the sheet URL).
    header_row: The row holding the headers (1-based, default 1).
    range: Optional worksheet pin — ``<sheetId>`` or ``<sheetId>!A1:B2`` (only
        its sheet part is used). Empty = the first worksheet.
    user_key: The sender's open_id (from ``<feishu_context>``).
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_sheet_find_columns(
    token: str,
    header_row: int = 1,
    range: str = "",
    user_key: str = "",
) -> str:
    """List the header row's columns: column letter + verbatim header (dates normalized)."""
    outcome = await _f.find_sheet_columns_impl(token=token, header_row=header_row, range_=range, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False)
