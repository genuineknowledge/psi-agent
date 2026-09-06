"""Handle the writer's confirmation card for a positive-negative case."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import runtime
from _positive_negative_list.drafts import delete_draft_body, save_draft
from _positive_negative_list.models import CaseDraft
from _positive_negative_list.notifications import (
    NotificationResult,
    NotificationSender,
)
from _positive_negative_list.runtime import configured_table_adapter
from _positive_negative_list.table import TableAdapter, WriteResult
from _positive_negative_list.validation import validate_case

from psi_agent._appdata import resolve_appdata_root
from psi_agent.session.runtime_context import get_session_id

TABLE_ADAPTER: TableAdapter | Any | None = None
table_adapter: TableAdapter | Any | None = None
_CONFIRM_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}


def _preview_digest(case: CaseDraft) -> str:
    # ``workflow`` is a durable implementation state, not part of the card
    # snapshot.  Excluding it keeps a confirmation callback valid when a
    # process resumes a draft after transitioning it to ``writing``.
    snapshot = case.to_mapping()
    snapshot.pop("workflow", None)
    body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _context(payload: dict[str, Any]) -> dict[str, Any]:
    context = _as_mapping(payload.get("business_context"))
    if context:
        return context
    # Some card runtimes wrap the snapshot one level below business_context.
    return _as_mapping(payload.get("card_snapshot"))


def _action_name(payload: dict[str, Any]) -> str:
    action = _as_mapping(payload.get("action"))
    value = _as_mapping(action.get("value"))
    return str(value.get("action") or action.get("action_id") or "").strip()


def _receipt_path(root: str | Path, case_id: str) -> Path:
    return Path(root) / "positive-negative-list" / "receipts" / f"{case_id}.json"


def _read_receipt(root: str | Path, case_id: str) -> dict[str, Any] | None:
    try:
        value = json.loads(_receipt_path(root, case_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _find_any_draft(root: str | Path, session_id: str, case_id: str) -> CaseDraft | None:
    """Locate a draft without trusting writer identity supplied by the callback."""
    base = Path(root) / "positive-negative-list" / "drafts"
    for path in base.glob("*/*.json") if base.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("case_id") != case_id
            or payload.get("session_id") != session_id
        ):
            continue
        try:
            return CaseDraft.from_mapping(payload["case"])
        except (TypeError, ValueError, KeyError):
            return None
    return None


def _adapter() -> Any:
    value = TABLE_ADAPTER if TABLE_ADAPTER is not None else table_adapter
    if value is None:
        return configured_table_adapter()
    return value


async def _send_subject_notice(
    case: CaseDraft, public_record_id: str, root: str | Path | None = None
) -> NotificationResult:
    return await NotificationSender(root).send_subject_notice(case, public_record_id)


async def _confirm_unlocked(card_action_json: str = "", user_key: str = "") -> str:
    """Confirm one card snapshot and create at most one public record."""
    try:
        payload = json.loads(card_action_json)
    except (TypeError, json.JSONDecodeError):
        return _f.dumps_result({"ok": False, "status": "invalid_callback"})
    if not isinstance(payload, dict) or not user_key.strip():
        return _f.dumps_result({"ok": False, "status": "unauthorized"})
    if _action_name(payload) != "positive_negative_case_confirm":
        return _f.dumps_result({"ok": False, "status": "invalid_callback"})

    context = _context(payload)
    case_id = str(context.get("case_id") or "").strip()
    supplied_digest = str(context.get("preview_digest") or context.get("digest") or "").strip()
    if not case_id or not supplied_digest:
        return _f.dumps_result({"ok": False, "status": "invalid_callback"})

    root = await resolve_appdata_root()
    receipt = _read_receipt(root, case_id)
    if receipt is not None:
        if str(receipt.get("writer_user_key") or "") != user_key:
            return _f.dumps_result({"ok": False, "status": "unauthorized"})
        if receipt.get("notification_status") == "notification_pending_retry":
            # The public row already exists. Resume only the durable private
            # notification receipt; never re-run the table write on a retry.
            retry = await NotificationSender(root).retry_notification(case_id)
            return _f.dumps_result(
                {
                    "ok": True,
                    "status": retry.status,
                    "case_id": case_id,
                    "public_record_id": receipt.get("public_record_id", ""),
                    "notification_status": retry.status,
                    **({"message_id": retry.message_id} if retry.message_id else {}),
                    **({"error": retry.error} if retry.error else {}),
                }
            )
        return _f.dumps_result(
            {
                "ok": True,
                "status": "already_written",
                "case_id": case_id,
                "public_record_id": receipt.get("public_record_id", ""),
                "notification_status": receipt.get("notification_status", ""),
            }
        )

    session_id = get_session_id()
    case = _find_any_draft(root, session_id, case_id)
    if case is None:
        return _f.dumps_result({"ok": False, "status": "draft_not_found"})
    if case.writer_user_key != user_key:
        return _f.dumps_result({"ok": False, "status": "unauthorized"})
    if case.workflow not in {"ready_for_confirmation", "writing"}:
        return _f.dumps_result({"ok": False, "status": "not_pending_confirmation"})
    if supplied_digest != _preview_digest(case):
        return _f.dumps_result({"ok": False, "status": "digest_mismatch"})
    errors = validate_case(case)
    if errors:
        return _f.dumps_result({"ok": False, "status": "validation_failed", "errors": list(errors)})
    if case.red_line_candidate:
        return _f.dumps_result({"ok": False, "status": "red_line_manual_review"})

    # The production writer is a robot-owned isolated test table.  Its
    # coordinates are created once in AppData; no new environment variable or
    # config field is required.  Tests may still inject an adapter directly.
    if TABLE_ADAPTER is None and table_adapter is None:
        try:
            await runtime.ensure_test_table(user_key)
        except Exception as exc:
            return _f.dumps_result({"ok": False, "status": "test_table_init_failed", "error": str(exc)})
    adapter = _adapter()
    try:
        preflight = await adapter.preflight(user_key)
    except Exception as exc:
        return _f.dumps_result({"ok": False, "status": "preflight_failed", "error": str(exc)})
    if not getattr(preflight, "ok", False):
        errors = getattr(preflight, "errors", ())
        return _f.dumps_result(
            {
                "ok": False,
                "status": "preflight_failed",
                "errors": list(errors) if errors else ["table preflight failed"],
            }
        )

    # Persist the transition before the external create call so a second callback
    # cannot race a first one into creating two rows.
    writing_case = CaseDraft.from_mapping(case.to_mapping() | {"workflow": "writing"})
    save_draft(root, user_key, session_id, case_id, writing_case)
    try:
        written = await adapter.create_public_record(writing_case, user_key)
    except Exception as exc:
        save_draft(root, user_key, session_id, case_id, case)
        return _f.dumps_result({"ok": False, "status": "write_failed", "error": str(exc)})

    if isinstance(written, WriteResult):
        if written.status == "possible_duplicate":
            save_draft(root, user_key, session_id, case_id, case)
            return _f.dumps_result(
                {"ok": False, "status": "possible_duplicate", "candidates": list(written.candidates)}
            )
        if written.status == "dedupe_failed":
            save_draft(root, user_key, session_id, case_id, case)
            return _f.dumps_result(
                {"ok": False, "status": "dedupe_failed", "error": written.error or "dedupe lookup failed"}
            )
        record = written.record or {}
    elif isinstance(written, dict):
        record = written
    else:
        record = {}
    public_record_id = str(record.get("record_id") or record.get("id") or "").strip()
    if not public_record_id:
        save_draft(root, user_key, session_id, case_id, case)
        return _f.dumps_result({"ok": False, "status": "write_failed", "error": "public record ID missing"})

    public_record_link = str(record.get("record_link") or public_record_id)
    sender = NotificationSender(root)
    sender.save_receipt(
        case,
        public_record_link,
        NotificationResult(False, "notification_pending_retry", error="notification delivery pending"),
    )
    # The body is removed only after a durable receipt exists; the receipt keeps
    # enough data to retry the notice card after a process interruption.  A
    # review is deliberately not started here: the subject starts it from the
    # "开始复盘" button on the notice card.
    delete_draft_body(root, user_key, case_id)
    try:
        # Keep the row ID in the card action payload so the callback can read
        # the test-table record; the human-facing receipt still stores the
        # clickable link separately.
        notice_result = await _send_subject_notice(case, public_record_id, root)
    except Exception as exc:
        notice_result = NotificationResult(False, "notification_pending_retry", error=f"{type(exc).__name__}: {exc}")
    if not isinstance(notice_result, NotificationResult):
        notice_result = NotificationResult(True, "notification_sent")
    sender.save_receipt(case, public_record_link, notice_result)
    return _f.dumps_result(
        {
            "ok": True,
            "status": notice_result.status if notice_result.status != "skipped_same_person" else "written",
            "case_id": case_id,
            "public_record_id": public_record_id,
            "notification_status": notice_result.status,
            "private_review_status": "not_started",
            "private_review_notification": "not_applicable",
        }
    )


async def positive_negative_case_confirm(card_action_json: str = "", user_key: str = "") -> str:
    """Serialize confirmation side effects for one case within this process."""
    try:
        payload = json.loads(card_action_json)
    except (TypeError, json.JSONDecodeError):
        return await _confirm_unlocked(card_action_json, user_key)
    if (
        not isinstance(payload, dict)
        or not user_key.strip()
        or _action_name(payload) != "positive_negative_case_confirm"
    ):
        return await _confirm_unlocked(card_action_json, user_key)
    context = _context(payload)
    case_id = str(context.get("case_id") or "").strip()
    if not case_id:
        return await _confirm_unlocked(card_action_json, user_key)
    root = await resolve_appdata_root()
    loop = asyncio.get_running_loop()
    key = (id(loop), str(root), case_id)
    lock = _CONFIRM_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        return await _confirm_unlocked(card_action_json, user_key)


__all__ = ["positive_negative_case_confirm"]
