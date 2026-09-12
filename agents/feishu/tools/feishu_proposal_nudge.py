"""方案跟进到点催办: 同一拍发私聊正文 + 进度卡.

``schedule_manage`` 的 ``fire=tool`` 一次只能调一个工具. 方案通过后的 -pre/-ddl
若只挂 ``feishu_message_send``, 到点只有纯文字、没有可勾进度卡.
本工具把两拍收成一次调用, 专供 ``proposal-writing-standard`` C.同步定时.
"""

from __future__ import annotations

import json
from typing import Any

import _feishu_impl as _f
from feishu_todo_card import feishu_todo_card_send


async def feishu_proposal_nudge(
    receive_id: str,
    text: str,
    items_json: str,
    title: str = "方案进度",
    subtitle: str = "",
    receive_id_type: str = "chat_id",
    user_key: str = "",
) -> str:
    """Send one follow-up nudge: plain text first, then a multi-row progress card.

    Use as ``schedule_manage`` ``fire=tool`` target for proposal ``-pre`` / ``-ddl``
    reminders. Text carries the催办文案; the card is the same progress checklist
    (勾选=自报, 不等于验收). Prefer this over bare ``feishu_message_send`` whenever
    the reminder is about a方案里程碑.

    ``items_json`` shape matches ``feishu_todo_card_send`` (title/detail/done/…).
    Bake the full milestone list into ``tool_args`` at create time — fire has no LLM.

    Args:
        receive_id: chat_id (oc_…) or open_id (ou_…) from ``<feishu_context>``.
        text: Reminder body (【方案提醒】/【方案跟进】…); must not claim 组织已验收.
        items_json: JSON array of progress rows for the card.
        title: Card header.
        subtitle: Line under the header (e.g. proposal_id + which gate).
        receive_id_type: Usually auto-detected from the id prefix.
        user_key: Optional; send as this person instead of the bot.
    """
    rid = receive_id.strip()
    body = text.strip()
    if not rid:
        return _f.dumps_result({"ok": False, "error": "receive_id is required"})
    if not body:
        return _f.dumps_result({"ok": False, "error": "text is required"})
    if not items_json.strip():
        return _f.dumps_result({"ok": False, "error": "items_json is required (progress card rows)"})

    msg_raw = await _f.send_message_impl(rid, body, receive_id_type, "")
    msg: dict[str, Any] = msg_raw if isinstance(msg_raw, dict) else {"raw": msg_raw}
    msg_ok = bool(msg.get("ok", True)) if isinstance(msg, dict) else True

    card_raw = await feishu_todo_card_send(
        receive_id=rid,
        items_json=items_json,
        title=title,
        subtitle=subtitle,
        receive_id_type=receive_id_type,
        user_key=user_key,
    )
    try:
        card: dict[str, Any] = json.loads(card_raw) if isinstance(card_raw, str) else {"raw": card_raw}
    except json.JSONDecodeError:
        card = {"ok": False, "error": card_raw if isinstance(card_raw, str) else "card parse failed"}

    card_ok = (
        bool(card.get("ok")) if isinstance(card, dict) and "ok" in card else not str(card_raw).startswith("[Error]")
    )

    return _f.dumps_result(
        {
            "ok": msg_ok and card_ok,
            "message": msg,
            "card": card if isinstance(card, dict) else {"raw": card_raw},
            "note": "text then progress card; tick=self-report, not acceptance",
        }
    )
