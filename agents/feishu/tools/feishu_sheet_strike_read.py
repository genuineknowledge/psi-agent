"""Feishu sheet strikethrough read — 读看板单元格的删除线(上级验收标记).

SOP v1.1: 上级验收通过的方式 = 把已完成且达标的 TODO 用删除线划掉。
普通读表会把富文本拍平、删除线样式丢失。本工具走「导出 xlsx → 解析单元格
富文本 strike」链路(实测验证可行),返回该单元格每个文本段的删除线状态。

Deterministic: 只读样式,不做任何完成度判断。
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_sheet_strike_read(
    board_link: str,
    person_name: str,
    cycle_date: str,
    user_key: str = "",
) -> str:
    """Read one person's cell strikethrough state for one cycle column.

    Args:
        board_link: The board's /wiki/ or /sheets/ URL (wiki links resolved internally).
        person_name: The person's display name as it appears in the 人名列.
        cycle_date: The cycle column header (e.g. ``9.4``); the matching header column.
        user_key: Identity for export/read (usual convention).

    Returns JSON: ``{"ok": true, "person": ..., "cycle_date": ..., "cell": "V23",
    "runs": [{"text": "...", "strike": true/false}], "struck_runs": n,
    "total_runs": n}`` — struck_runs counts runs with strike=true.
    """
    if not board_link.strip():
        return _f.dumps_result(_f._error("board_link is required."))
    if not person_name.strip():
        return _f.dumps_result(_f._error("person_name is required."))
    if not cycle_date.strip():
        return _f.dumps_result(_f._error("cycle_date is required (the column header, e.g. 9.4)."))

    outcome = await _f.sheet_strike_read_impl(board_link.strip(), person_name.strip(), cycle_date.strip(), user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
