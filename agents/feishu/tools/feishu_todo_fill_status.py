"""Feishu TODO fill status — deterministic 谁没写 todo pipeline.

The 缺写 judgment (blank cell → leave-check → resigned-filter) kept drifting when
left to the model in free conversation. This tool runs the whole pipeline in
code — read the board, group by mentor, classify names against the directory,
check leaves — and returns five buckets the answer must be read from:

    缺写 / 请假免填 / 解析失败 / 已填

已离职/冻结人员在工具内部剔除、不返回——调用方拿不到名字,输出天然不体现。
Callers (chat or scheduled tasks) only relay the result; no judgment step is
left to the model.
"""

from __future__ import annotations

import json

import _feishu_impl as _f

_LEAVE_CODE = "99EEC396-536A-4C7A-8B2D-412584E35CE3"


async def feishu_todo_fill_status(
    board_link: str,
    cycle_date: str,
    mentor_name: str = "",
    user_key: str = "",
) -> str:
    """Return the five fill-status buckets for a cycle column of the TODO board.

    Args:
        board_link: The board's /wiki/ or /sheets/ URL (wiki links are resolved
            to the spreadsheet token internally).
        cycle_date: The cycle column header, as it appears on the sheet
            (e.g. ``9.9``); the newest such column is used when it repeats.
        mentor_name: Optional mentor name; empty = the whole board.
        user_key: Identity for board/leave/directory reads (usual convention).

    Returns JSON: ``{"ok": true, "cycle_date": ..., "缺写": [names], "请假免填": [...],
    "解析失败": [...], "已填": [...]}``.
    """
    if not board_link.strip():
        return _f.dumps_result(_f._error("board_link is required."))
    if not cycle_date.strip():
        return _f.dumps_result(_f._error("cycle_date is required (the column header, e.g. 9.9)."))

    outcome = await _f.todo_fill_status_impl(board_link.strip(), cycle_date.strip(), mentor_name.strip(), user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
