"""Read a Feishu spreadsheet in row blocks with explicit coordinates — no silent truncation.

This is the structured reader for fact questions about sheet data (who filled
what, how many rows, per-person contents). Unlike ``feishu_sheet_read`` (which
returns tab-separated text and truncates silently at a character budget), this
tool returns one block of rows per call with exact row coordinates and an
explicit ``has_more`` flag. The caller MUST keep reading from ``next_start_row``
until ``has_more`` is false — answering from a partial block is the single most
common correctness bug (whole columns missing, people reported as "empty" when
their rows were never read).

To answer "who is X's mentor" / "how many todo items does X have" / "compare A
and B": locate the columns with ``feishu_sheet_find_columns`` first, then read
the needed rows/columns with this tool. Rows are 1-based and match the sheet's
own row numbers.

Args:
    token: The spreadsheet_token (from the sheet URL).
    range: Optional worksheet pin — ``<sheetId>`` (whole first rows of that
        sheet) or ``<sheetId>!A1:B30`` (block pinned to that range's sheet).
        Empty = the spreadsheet's first worksheet.
    max_rows: Rows per block (default 50). The block is ``A{start_row}:{max}``.
    start_row: First row of the block (1-based, default 1). Use the previous
        result's ``next_start_row`` to continue.
    user_key: The sender's open_id (from ``<feishu_context>``).
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_sheet_read_grid(
    token: str,
    range: str = "",
    max_rows: int = 50,
    start_row: int = 1,
    user_key: str = "",
) -> str:
    """Read one block of rows as a structured grid with has_more/next_start_row."""
    outcome = await _f.read_sheet_grid_impl(
        token=token, range_=range, max_rows=max_rows, start_row=start_row, user_key=user_key
    )
    return json.dumps(outcome, ensure_ascii=False)
