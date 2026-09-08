"""Feishu TODO compare card — fixed-layout cycle compare card for the 16:00 push.

The 16:00 dynamic-ledger push delivers a per-mentor before/after comparison of this
cycle's TODOs. The card shape must NOT drift between runs (an LLM assembling card
JSON inline reorders columns or drops the board link occasionally), so this tool
owns the layout: caller passes rows + a few labels, the code builds the exact card
JSON (schema 2.0, one markdown element with a GFM table) and sends it.

Table columns are fixed: 成员 / 上期 / 本期 / 搞定情况(上期=上一期填报, 周期不固定, 不写具体是哪一日). Below the
table sit, in order: an optional notes line (period/definition/未填报名单等说明), the
alignment-pending notes, and the board link.
"""

from __future__ import annotations

import json

import _feishu_impl as _f

_BOARD_LINK = "https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc"
_REMINDER_LINE = "请检查你手下成员的当期填报是否合理:"

# 卡片布局契约:表格四列 + 提醒句/存疑/链接三段,顺序与列名都不许漂移。
_TABLE_HEADER = "| 成员 | 上期 | 本期 | 搞定情况 |"
_TABLE_SEPARATOR = "|---|---|---|---|"


def _md_cell(value: str) -> str:
    """Escape one GFM table cell: pipes and newlines break the table otherwise."""
    return (value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _build_card_json(mentor_name: str, cycle_date: str, rows: list[dict], align_notes: str, notes: str = "") -> dict:
    lines = [
        _REMINDER_LINE,
        "",
        _TABLE_HEADER,
        _TABLE_SEPARATOR,
    ]
    for row in rows:
        lines.append(
            f"| {_md_cell(str(row.get('member', '')))} "
            f"| {_md_cell(str(row.get('prev', '')))} "
            f"| {_md_cell(str(row.get('curr', '')))} "
            f"| {_md_cell(str(row.get('status', '')))} |"
        )
    lines.append("")
    if notes.strip():
        lines.append(f"说明:{_md_cell(notes.strip())}")
        lines.append("")
    if align_notes.strip():
        lines.append(f"**对齐存疑**:{_md_cell(align_notes.strip())}")
        lines.append("")
    lines.append(f"看板表: {_BOARD_LINK}")
    markdown = "\n".join(lines)

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"TODO 前后对比 · {mentor_name}组({cycle_date}期)"},
            "template": "blue",
        },
        "body": {"elements": [{"tag": "markdown", "content": markdown}]},
    }


async def feishu_todo_compare_card_send(
    receive_id: str,
    mentor_name: str,
    cycle_date: str,
    rows_json: str,
    receive_id_type: str = "open_id",
    align_notes: str = "",
    notes: str = "",
    user_key: str = "",
) -> str:
    """Send the fixed-layout cycle compare card to one mentor.

    Args:
        receive_id: Recipient id (mentor's open_id for private chat).
        mentor_name: Mentor display name (card title: ``TODO 前后对比 · <mentor>组(<date>期)``).
        cycle_date: Cycle date string used in the title.
        rows_json: JSON array of row objects, one per member:
            ``{"member": "张三", "prev": "上期条目简写", "curr": "本期条目简写", "status": "搞定情况"}``.
            prev/curr may join multiple items with 「;」; status uses 搞定/进行中/新开/消失待确认/请假顺延.
        receive_id_type: ``open_id`` (private) / ``chat_id`` (group); auto-corrected on send.
        align_notes: Optional alignment-pending notes (from align-pending.txt), rendered
            as one 「**对齐存疑**」 line below the table; empty = omitted.
        notes: Optional explanation line rendered as 「说明:...」 right below the table —
            period dates (上期=9.4、本期=9.7), 搞定定义, 未填报名单, 回流计数, and the
            mobile hint 「手机上点表格行可展开查看详情」. Empty = omitted.
        user_key: Identity for the send (usual convention); omitted uses the bot.
    """
    if not receive_id.strip():
        return _f.dumps_result(_f._error("receive_id is required (the mentor's open_id)."))
    if not mentor_name.strip():
        return _f.dumps_result(_f._error("mentor_name is required."))
    try:
        rows = json.loads(rows_json)
    except json.JSONDecodeError as exc:
        return _f.dumps_result(_f._error(f"rows_json must be valid JSON: {exc}"))
    if not isinstance(rows, list):
        return _f.dumps_result(_f._error("rows_json must be a JSON array of row objects."))

    card = _build_card_json(mentor_name.strip(), cycle_date.strip(), rows, align_notes, notes)
    outcome = await _f.send_card_impl(
        receive_id=receive_id.strip(),
        card_json=json.dumps(card, ensure_ascii=False),
        receive_id_type=receive_id_type,
        user_key=user_key,
    )
    return json.dumps(outcome, ensure_ascii=False, default=str)
