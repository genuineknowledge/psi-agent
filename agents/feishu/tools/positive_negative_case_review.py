"""Start and submit a private three-part positive-negative case review."""

# ruff: noqa: E402

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import reviews
from _positive_negative_list.models import LedgerRecord
from _positive_negative_list.notifications import _trusted_identities
from _positive_negative_list.runtime import configured_read_table_adapter as configured_table_adapter

from psi_agent._appdata import resolve_appdata_root as _resolve_appdata_root


def _parse_record(value: str) -> LedgerRecord:
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"record_json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("record_json must be a JSON object")
    return LedgerRecord.from_mapping(raw)


def _with_subject(record: LedgerRecord, subject_user_key: str) -> LedgerRecord:
    fields = dict(record.fields)
    fields["涉事人"] = subject_user_key
    return LedgerRecord.from_mapping(record.to_mapping() | {"subject_user_key": subject_user_key, "fields": fields})


async def _resolve_record(record_json: str, record_id: str, user_key: str) -> LedgerRecord | None:
    if record_json.strip():
        return _parse_record(record_json)
    if record_id.strip():
        return await configured_table_adapter().get_record(record_id.strip(), user_key)
    raise ValueError("record_json or record_id is required")


async def positive_negative_case_review_start(
    record_json: str = "",
    record_id: str = "",
    subject_user_key: str = "",
    user_key: str = "",
) -> str:
    """Start a private three-question review for a negative ledger record.

    Args:
        record_json: Inline normalized record JSON.
        record_id: Existing Feishu record ID when ``record_json`` is omitted.
        subject_user_key: Optional recipient override for a single subject.
        user_key: Trusted Feishu sender identity.

    Returns:
        JSON review ID and notification status; answers remain private.
    """
    try:
        record = await _resolve_record(record_json, record_id, user_key)
        if record is None:
            return _f.dumps_result({"ok": False, "status": "record_not_found"})
        if record.nature != "negative":
            return _f.dumps_result({"ok": False, "status": "review_not_negative"})
        if subject_user_key.strip() and subject_user_key.strip() != record.subject_user_key:
            record = _with_subject(record, subject_user_key.strip())
        subjects, subject_error = _trusted_identities(record.subject_user_key)
        if subject_error:
            status = (
                "notification_identity_missing"
                if not record.subject_user_key.strip()
                else "notification_identity_unresolved"
            )
            return _f.dumps_result({"ok": False, "status": status, "error": subject_error})
        if len(subjects) != 1:
            return _f.dumps_result(
                {"ok": False, "status": "review_subject_ambiguous", "error": "复盘一次只支持一个涉事人"}
            )
        if not user_key.strip():
            return _f.dumps_result({"ok": False, "status": "unauthorized", "error": "user identity is required"})
        if user_key.strip() != subjects[0]:
            return _f.dumps_result({"ok": False, "status": "review_owner_mismatch"})

        root = await _resolve_appdata_root()
        existing = next(
            (
                item
                for item in reviews.find_active_reviews(root, record.subject_user_key)
                if item.record_id == record.record_id
            ),
            None,
        )
        if existing is not None:
            return _f.dumps_result(
                {
                    "ok": True,
                    "status": "review_already_started",
                    "review_id": existing.review_id,
                    "record_id": record.record_id,
                }
            )
        review_id = f"review_{secrets.token_urlsafe(12)}"
        # The review is addressed to the subject.  The requester may be the
        # reporter or writer, but only the subject should be able to submit the
        # private reflection answers.
        draft = reviews.new_review(record, record.subject_user_key, review_id)
        try:
            sent = await reviews.send_message_impl(
                subjects[0],
                reviews.review_prompt(record),
                "open_id",
            )
        except Exception as exc:
            return _f.dumps_result(
                {"ok": False, "status": "review_notification_failed", "review_id": review_id, "error": str(exc)}
            )
        if not isinstance(sent, dict) or not sent.get("ok"):
            message = sent.get("message") if isinstance(sent, dict) else "review notification failed"
            return _f.dumps_result(
                {
                    "ok": False,
                    "status": "review_notification_failed",
                    "review_id": review_id,
                    "error": str(message or "review notification failed"),
                }
            )
        reviews.save_review(root, draft)
        return _f.dumps_result(
            {
                "ok": True,
                "status": "review_started",
                "review_id": review_id,
                "record_id": record.record_id,
                "message_id": str(sent.get("message_id") or ""),
            }
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _f.dumps_result({"ok": False, "status": "invalid_record", "error": str(exc)})
    except (OSError, RuntimeError) as exc:
        return _f.dumps_result({"ok": False, "status": "record_lookup_failed", "error": f"{type(exc).__name__}: {exc}"})


async def positive_negative_case_review_submit(
    review_id: str = "",
    objective_reason: str = "",
    corrective_action: str = "",
    prevention_plan: str = "",
    user_key: str = "",
) -> str:
    """Save the subject's three-part review answers in private AppData.

    Args:
        review_id: ID returned by ``positive_negative_case_review_start``.
        objective_reason: Objective cause or constraint.
        corrective_action: Concrete remediation action.
        prevention_plan: Future prevention or reminder plan.
        user_key: Trusted subject identity submitting the review.

    Returns:
        JSON submission status and coaching feedback.
    """
    try:
        root = await _resolve_appdata_root()
        if review_id.strip():
            draft = reviews.load_review(root, review_id)
        else:
            if not user_key.strip():
                return _f.dumps_result({"ok": False, "status": "review_id_required"})
            active = reviews.find_active_reviews(root, user_key.strip())
            if len(active) > 1:
                return _f.dumps_result(
                    {"ok": False, "status": "review_ambiguous", "error": "当前有多条待复盘记录, 请指定复盘对象"}
                )
            draft = active[0] if active else None
        if draft is None:
            return _f.dumps_result({"ok": False, "status": "review_not_found"})
        if not user_key.strip() or draft.writer_user_key != user_key.strip():
            return _f.dumps_result({"ok": False, "status": "review_owner_mismatch"})
        answers = {
            "objective_reason": objective_reason.strip(),
            "corrective_action": corrective_action.strip(),
            "prevention_plan": prevention_plan.strip(),
        }
        missing = [name for name, value in answers.items() if not value]
        if missing:
            return _f.dumps_result({"ok": False, "status": "review_incomplete", "missing": missing})
        submitted = reviews.ReviewDraft(
            review_id=draft.review_id,
            record_id=draft.record_id,
            subject_user_key=draft.subject_user_key,
            writer_user_key=draft.writer_user_key,
            started_at=draft.started_at,
            objective_reason=answers["objective_reason"],
            corrective_action=answers["corrective_action"],
            prevention_plan=answers["prevention_plan"],
            status="submitted",
        )
        reviews.save_review(root, submitted)
        return _f.dumps_result(
            {
                "ok": True,
                "status": "review_submitted",
                "review_id": draft.review_id,
                "record_id": draft.record_id,
                "feedback": reviews.review_feedback(submitted),
            }
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _f.dumps_result({"ok": False, "status": "invalid_review", "error": str(exc)})


__all__ = ["positive_negative_case_review_start", "positive_negative_case_review_submit"]
