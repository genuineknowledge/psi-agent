"""Manually remind a subject about an existing positive-negative ledger record."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _assignment_display import resolve_people_display
from _positive_negative_list import notifications
from _positive_negative_list.models import LedgerRecord
from _positive_negative_list.runtime import configured_read_table_adapter as configured_table_adapter
from positive_negative_case_review import positive_negative_case_review_start

from psi_agent._appdata import resolve_appdata_root as _resolve_appdata_root


def _parse_record(value: str) -> LedgerRecord:
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"record_json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("record_json must be a JSON object")
    return LedgerRecord.from_mapping(raw)


async def positive_negative_case_remind(
    record_json: str = "",
    record_id: str = "",
    subject_user_key: str = "",
    force: bool = False,
    user_key: str = "",
    card_action_json: str = "",
) -> str:
    """Privately remind the subject of an existing ledger record.

    Args:
        record_json: Inline normalized record JSON.
        record_id: Existing Feishu record ID when ``record_json`` is omitted.
        subject_user_key: Optional trusted recipient override.
        force: Re-send even when an idempotency receipt already exists.
        user_key: Trusted Feishu sender identity used to resolve records.

    Returns:
        JSON delivery status; failures remain retryable and do not modify the table.
    """
    try:
        if card_action_json.strip():
            try:
                envelope = json.loads(card_action_json)
            except json.JSONDecodeError as exc:
                return _f.dumps_result({"ok": False, "status": "invalid_callback", "error": str(exc)})
            if not isinstance(envelope, dict):
                return _f.dumps_result({"ok": False, "status": "invalid_callback"})
            action = envelope.get("action") if isinstance(envelope.get("action"), dict) else {}
            value = action.get("value") if isinstance(action.get("value"), dict) else {}
            action_name = str(value.get("action") or action.get("action_id") or "").strip()
            if action_name != "pn_record_review_start":
                return _f.dumps_result({"ok": False, "status": "invalid_callback"})
            source = envelope.get("source") if isinstance(envelope.get("source"), dict) else {}
            operator_payload = envelope.get("operator") if isinstance(envelope.get("operator"), dict) else {}
            operator = str(
                source.get("operator_open_id") or source.get("open_id") or operator_payload.get("open_id") or ""
            ).strip()
            if operator and user_key.strip() and operator != user_key.strip():
                return _f.dumps_result({"ok": False, "status": "unauthorized"})
            operator = operator or user_key.strip()
            record_id = str(value.get("record_id") or "").strip()
            subject = str(value.get("subject_user_key") or "").strip()
            if not record_id or not subject or not operator or operator != subject:
                return _f.dumps_result({"ok": False, "status": "unauthorized"})
            return await positive_negative_case_review_start(
                record_id=record_id,
                subject_user_key=subject,
                user_key=operator,
            )
        if record_json.strip():
            record = _parse_record(record_json)
        elif record_id.strip():
            try:
                record = await configured_table_adapter().get_record(record_id.strip(), user_key)
            except Exception as exc:
                return _f.dumps_result(
                    {
                        "ok": False,
                        "status": "record_lookup_failed",
                        "record_id": record_id.strip(),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if record is None:
                return _f.dumps_result({"ok": False, "status": "record_not_found", "record_id": record_id.strip()})
        else:
            raise ValueError("record_json or record_id is required")
        if subject_user_key.strip() and subject_user_key.strip() != record.subject_user_key:
            record = replace(record, subject_user_key=subject_user_key.strip())
        root = await _resolve_appdata_root()
        result = await notifications.NotificationSender(root).send_record_notice(record, force=force)
        subject_display = await resolve_people_display(record.subject_user_key, _f.get_users_batch_impl)
        return _f.dumps_result(
            {
                "ok": result.ok,
                "status": result.status,
                "record_id": record.record_id,
                "涉事人": subject_display,
                **({"message_id": result.message_id} if result.message_id else {}),
                **({"error": result.error} if result.error else {}),
            }
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _f.dumps_result({"ok": False, "status": "invalid_record", "error": str(exc)})


__all__ = ["positive_negative_case_remind"]
