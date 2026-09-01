from __future__ import annotations

import json
from typing import Any

import _feishu_impl
from _assignment_delivery import advance_delivery as _advance_delivery
from _assignment_delivery import result_object
from _assignment_delivery import sync_progress_card as _sync_progress_card
from _assignment_tool_common import CLIENT, dumps_result, invalid_argument


async def assignment_delivery_refresh(event_payload_json: str = "{}") -> str:
    """Internal trigger handler that refreshes pending assignment delivery read status.

    This is invoked by the assignment delivery synthetic event with ``fire=tool``.
    Normal conversations and assignment skills must not call it directly.

    Args:
        event_payload_json: Synthetic event payload injected by the trigger runtime.
    """
    try:
        event_payload = json.loads(event_payload_json)
    except json.JSONDecodeError:
        return invalid_argument("event_payload_json must be a JSON object string")
    if not isinstance(event_payload, dict):
        return invalid_argument("event_payload_json must be a JSON object")

    pending_result = await CLIENT.call_tool(
        "assignment_delivery",
        {"action": "list_pending", "payload": {"limit": 50}},
        retryable=True,
    )
    deliveries = pending_result.get("result")
    if pending_result.get("ok") is not True or not isinstance(deliveries, list):
        return dumps_result(pending_result)

    checked = 0
    read_advanced = 0
    card_updates = 0
    errors: list[dict[str, Any]] = []
    for raw_delivery in deliveries:
        if not isinstance(raw_delivery, dict):
            continue
        assignment_id = _text(raw_delivery.get("assignment_id"))
        if assignment_id is None:
            errors.append({"assignment_id": assignment_id or "", "code": "invalid_delivery"})
            continue
        try:
            assignment_result = await CLIENT.call_tool(
                "assignment_get",
                {"assignment_id": assignment_id},
                retryable=True,
            )
            assignment = result_object(assignment_result)
            title = _text(assignment.get("title")) if assignment is not None else None
            if assignment is None or title is None:
                errors.append({"assignment_id": assignment_id, "code": "assignment_unavailable"})
                continue

            if raw_delivery.get("task_published_at") is None and _task_is_published(assignment):
                published = await _advance_delivery(
                    CLIENT,
                    assignment_id=assignment_id,
                    event="task_published",
                )
                if result_object(published) is None:
                    errors.append({"assignment_id": assignment_id, "code": _error_code(published)})

            recipients = raw_delivery.get("recipients")
            if not isinstance(recipients, list):
                errors.append({"assignment_id": assignment_id, "code": "invalid_recipients"})
                continue
            for recipient in recipients:
                if (
                    not isinstance(recipient, dict)
                    or recipient.get("send_status") != "sent"
                    or recipient.get("accepted_at")
                    or recipient.get("read_at")
                ):
                    continue
                open_id = _recipient_open_id(recipient)
                message_id = _text(recipient.get("message_id"))
                if open_id is None or message_id is None:
                    continue
                read_status = await _feishu_impl.read_status_impl(
                    message_id,
                    False,
                    100,
                    "",
                )
                if read_status.get("ok") is not True:
                    errors.append(
                        {
                            "assignment_id": assignment_id,
                            "recipient_open_id": open_id,
                            "code": _error_code(read_status),
                        }
                    )
                    continue
                reader = _reader(read_status.get("read_users"), open_id)
                if reader is None:
                    continue
                advanced = await _advance_delivery(
                    CLIENT,
                    assignment_id=assignment_id,
                    event="read",
                    recipient_open_id=open_id,
                )
                if result_object(advanced) is not None:
                    read_advanced += 1
                else:
                    errors.append({"assignment_id": assignment_id, "code": _error_code(advanced)})

            synced = await _sync_progress_card(
                CLIENT,
                assignment_id=assignment_id,
                title=title,
            )
            checked += 1
            if synced.get("ok") is True and synced.get("updated") is True:
                card_updates += 1
            elif synced.get("ok") is not True:
                errors.append({"assignment_id": assignment_id, "code": _error_code(synced)})
        except Exception as exc:
            errors.append(
                {
                    "assignment_id": assignment_id,
                    "code": "operation_failed",
                    "message": str(exc),
                }
            )

    return dumps_result(
        {
            "ok": True,
            "checked": checked,
            "read_advanced": read_advanced,
            "card_updates": card_updates,
            "errors": errors,
        }
    )


def _reader(value: Any, open_id: str) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    return next(
        (item for item in value if isinstance(item, dict) and _text(item.get("open_id")) == open_id),
        None,
    )


def _recipient_open_id(recipient: dict[str, Any]) -> str | None:
    delivery_open_id = _text(recipient.get("delivery_open_id"))
    if delivery_open_id is not None:
        return delivery_open_id
    open_ids = recipient.get("open_ids")
    if not isinstance(open_ids, list):
        return None
    return next((text for item in open_ids if (text := _text(item))), None)


def _task_is_published(assignment: dict[str, Any]) -> bool:
    records = assignment.get("delivery_records")
    return isinstance(records, list) and any(
        isinstance(record, dict)
        and record.get("channel") == "feishu_task"
        and record.get("status") == "published"
        and _text(record.get("task_guid")) is not None
        for record in records
    )


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _error_code(value: dict[str, Any]) -> str:
    error = value.get("error")
    if isinstance(error, dict):
        code = _text(error.get("code"))
        if code is not None:
            return code
    return _text(value.get("code")) or "operation_failed"
