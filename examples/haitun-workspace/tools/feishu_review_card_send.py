"""Resend a review card for a ledger row (ops / re-issue helper).

Called by the agent when a review card needs to be sent again for an already
delivered todo (e.g. the first card was missed, or a test re-run). Reads the
ledger row with tenant identity, then hands off to
``_review_card_impl._send_review_card``, which resolves the row's mentor and
sends the card (in test mode the ``PSI_REVIEW_CARD_TEST_RECEIVE_ID`` env var
overrides the receiver — never send to a real mentor while testing).
"""

from __future__ import annotations

import json

import _review_card_impl as _review


async def feishu_review_card_send(
    record_id: str = "",
    ledger_app_token: str = "",
    ledger_table_id: str = "",
    user_key: str = "",
) -> str:
    """Send a review card for one ledger row.

    Args:
        record_id: Ledger record id of the delivered todo.
        ledger_app_token: Bit table app token (required; no hardcoded fallback).
        ledger_table_id: Bit table table id (required; no hardcoded fallback).
        user_key: Clicker's open_id (injected by Session).
    """
    app_token = ledger_app_token.strip()
    table_id = ledger_table_id.strip()
    # 坐标缺失时显式报错,不回退到任何硬编码表。
    if not app_token or not table_id:
        return json.dumps({"ok": False, "error": "ledger_app_token/ledger_table_id required"}, ensure_ascii=False)
    record_id = record_id.strip()
    if not record_id:
        return json.dumps({"ok": False, "error": "record_id required"}, ensure_ascii=False)

    row = await _review._fetch_ledger_row(app_token, table_id, record_id, user_key)
    if not row.get("ok"):
        return json.dumps(
            {"ok": False, "error": row.get("error") or row.get("message") or "ledger read failed"},
            ensure_ascii=False,
            default=str,
        )
    fields = row.get("fields", {}) or {}
    title = _review._cell_text(fields.get("标题")) or "该待办"
    task_guid = _review._cell_text(fields.get("任务GUID"))

    value = {
        "ledger_record_id": record_id,
        "ledger_app_token": app_token,
        "ledger_table_id": table_id,
    }
    outcome = await _review._send_review_card(value=value, title=title, task_guid=task_guid, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
