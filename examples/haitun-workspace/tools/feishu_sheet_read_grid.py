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
        ⚠️ 事实问答只传 ``<sheetId>``,不要钉死行范围——范围钉死时 has_more
        只能报告该范围内的情况,范围外的行永远读不到(已实测事故:钉死 A1:S20
        导致第 31 行的人漏读)。
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
    """Read a spreadsheet in row blocks with exact coordinates — the reader for fact questions.

    **Prefer this over ``feishu_sheet_read`` for any question about who filled what**
    (per-person contents, "谁没填", how many rows, comparing two people). That tool
    stops at a character budget and drops whole rows, so on a real board it comes back
    partial; this one returns a block plus an explicit ``has_more`` / ``next_start_row``
    so nothing is lost quietly.

    Recipe for a 列=日期、行=人 board — locate first, then fetch, instead of pulling the
    whole sheet:

    1. ``feishu_sheet_find_columns`` (or read just the name column) to get the person's
       **row number** and the target **column letter**;
    2. read that one cell / row with this tool or a pinned range.

    Pulling the whole board first is what makes a read come back truncated; locating
    first keeps every read small.

    **Keep reading until ``has_more`` is false.** Answering from one partial block is the
    single most common correctness bug here: unread rows look like empty cells, so people
    get reported as not having filled anything when their row was simply never fetched.
    Row numbers are 1-based and line up with the sheet's own rows.
    """
    outcome = await _f.read_sheet_grid_impl(
        token=token, range_=range, max_rows=max_rows, start_row=start_row, user_key=user_key
    )
    if outcome.get("ok"):
        # 列字母表头 + 行号首列内嵌:对齐由数据自证,LLM 不用手数。
        outcome = _f._label_grid(outcome)
    return json.dumps(outcome, ensure_ascii=False)
