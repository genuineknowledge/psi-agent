"""Locate a sheet's header row and classify every column's semantics in code.

Counting header cells by eye is a proven failure mode (columns misidentified,
person rows read as empty). This tool reads the header row and classifies each
non-empty header deterministically:

- ``kind: "date"`` — cycle columns like 7.24 / 8.10日 / 2026-08-14, with the
  normalized ISO date (``date`` field);
- ``kind: "names"`` — the person/owner column (负责人/姓名/名字/owner);
- ``kind: "mentor"`` — the mentor column;
- ``kind: "other"`` — anything else (kept in the list, not dropped).

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
    """Classify the header row's columns: date/names/mentor/other with letters."""
    outcome = await _f.find_sheet_columns_impl(token=token, header_row=header_row, range_=range, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False)
