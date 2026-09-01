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
    range: Optional worksheet pin, plus optional **column** narrowing —
        ``<sheetId>`` reads the full ``A:ZZ`` width; ``<sheetId>!B1:O80`` reads
        only columns B..O. **Row numbers in this argument are ignored** — paging
        is driven by ``start_row`` / ``max_rows``, so pinning columns can never
        hide rows outside the range (the old 钉死 A1:S20 → 第 31 行漏读 事故 is
        structurally impossible now). Empty = the first worksheet, full width.
        ⚠️ 只读需要的列:一个 20 列的看板整宽拉回来动辄几十万字符,会撞上
        每条结果的字符上限,逼工具少给你几行(has_more 会如实报告,但要多跑
        几轮)。已知人名列和目标日期列时,narrow 到那两列最省。
    max_rows: Rows per block (default 50). The block spans the pinned columns
        from ``start_row`` to ``start_row + max_rows - 1``.
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
    2. read just those columns with ``range="<sheetId>!B1:B80"`` (name column) or
       ``"<sheetId>!O1:O80"`` (one date column) — the column letters in ``range`` are
       honored, so a narrow read really is narrow.

    Pulling the whole board's width is what forces this tool to hand back fewer rows per
    block; narrowing the columns keeps every read small and the paging short.

    **Keep reading until ``has_more`` is false.** Answering from one partial block is the
    single most common correctness bug here: unread rows look like empty cells, so people
    get reported as not having filled anything when their row was simply never fetched.
    Row numbers are 1-based and line up with the sheet's own rows. A block may come back
    with fewer rows than ``max_rows`` when the text is wide — that is reported honestly via
    ``has_more`` / ``next_start_row``, so just keep paging; it is never a silent cut.

    To decide whether person X wrote on date D: the result carries ``filled_cols`` — a
    per-row list of column letters whose cells are non-empty, computed in code. Check
    that list against the header's date column (date → column letter via
    ``feishu_sheet_find_columns``). **Never infer a date column is filled from a date
    number inside another cell's text** — e.g. a todo cell mentioning "(8.24)" is just
    content, not evidence the 8.24 column was written.

    To read *what* person X wrote on date D: a single-row read (one data row) also
    carries ``cells`` — a per-row map of column letter → cell text, computed in code.
    Fetch the text by its column-letter **key**; **never pick the Nth element out of
    the ``rows`` array** — adjacent long todo texts all look alike, and miscounting by
    one lands you on the neighbouring column (a date correctly located but the wrong
    content). Multi-row reads omit ``cells`` (size); re-read that single row first.
    """
    outcome = await _f.read_sheet_grid_impl(
        token=token, range_=range, max_rows=max_rows, start_row=start_row, user_key=user_key
    )
    if outcome.get("ok"):
        # 列字母表头 + 行号首列内嵌:对齐由数据自证,LLM 不用手数。
        outcome = _f._label_grid(outcome)
    return json.dumps(outcome, ensure_ascii=False)
