from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import _feishu_impl


def parse_tool_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {"ok": False, "error": {"code": "invalid_tool_result"}}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "invalid_tool_result"}}
    return value if isinstance(value, dict) else {"ok": False, "error": {"code": "invalid_tool_result"}}


def progress_card(title: str, delivery: dict[str, Any]) -> dict[str, Any]:
    recipients = delivery.get("recipients")
    tracked = [item for item in recipients if isinstance(item, dict)] if isinstance(recipients, list) else []
    total = len(tracked)
    sent = sum(
        item.get("send_status") == "sent" or bool(item.get("message_id") or item.get("sent_at")) for item in tracked
    )
    read = sum(bool(item.get("read_at")) for item in tracked)
    accepted = sum(bool(item.get("accepted_at")) for item in tracked)
    published = bool(delivery.get("task_published_at"))
    lines = [
        f"已发送: {sent}/{total}",
        f"已读 (若可获取): {read}/{total}",
        f"已确认接收: {accepted}/{total}",
        f"飞书任务已创建: {'是' if published else '否'}",
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "任务接收进度"},
            "template": "green" if published else "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"任务: {title}"},
            },
            {"tag": "markdown", "content": "\n".join(lines)},
        ],
    }


async def update_progress_card(title: str, delivery: dict[str, Any]) -> dict[str, Any]:
    message_id = _text(delivery.get("assigner_progress_message_id"))
    assigner_open_id = _text(delivery.get("assigner_open_id")) or ""
    if message_id is None:
        return {
            "ok": False,
            "error": {
                "code": "assignment_progress_card_missing",
                "message": "Assignment delivery has no progress card message id",
                "retryable": False,
            },
        }
    return await _feishu_impl.edit_card_impl(
        message_id,
        json.dumps(progress_card(title, delivery), ensure_ascii=False),
        assigner_open_id,
    )


async def advance_delivery(
    client: Any,
    *,
    assignment_id: str,
    event: str,
    recipient_open_id: str = "",
    max_attempts: int = 3,
) -> dict[str, Any]:
    for _attempt in range(max_attempts):
        fetched = await client.call_tool(
            "assignment_delivery",
            {"action": "get", "assignment_id": assignment_id},
            retryable=True,
        )
        current = result_object(fetched)
        if current is None:
            return fetched
        revision = current.get("revision")
        if not isinstance(revision, int):
            return _invalid_delivery("Assignment delivery has no revision")
        payload: dict[str, Any] = {
            "event": event,
            "occurred_at": datetime.now(UTC).isoformat(),
            "expected_revision": revision,
        }
        if recipient_open_id:
            payload["recipient_open_id"] = recipient_open_id
        advanced = await client.call_tool(
            "assignment_delivery",
            {
                "action": "advance",
                "assignment_id": assignment_id,
                "payload": payload,
            },
            retryable=False,
        )
        if result_object(advanced) is not None or not _is_revision_conflict(advanced):
            return advanced
    return _invalid_delivery("Assignment delivery changed too frequently", retryable=True)


async def sync_progress_card(
    client: Any,
    *,
    assignment_id: str,
    title: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    force_render = False
    for _attempt in range(max_attempts):
        fetched = await client.call_tool(
            "assignment_delivery",
            {"action": "get", "assignment_id": assignment_id},
            retryable=True,
        )
        delivery = result_object(fetched)
        if delivery is None:
            return fetched
        revision = delivery.get("revision")
        rendered_revision = delivery.get("card_rendered_revision", 0)
        if not isinstance(revision, int) or not isinstance(rendered_revision, int):
            return _invalid_delivery("Assignment delivery has invalid render revisions")
        if delivery.get("progress_status") != "sent":
            return _invalid_delivery("Assignment progress card has not been sent")
        if rendered_revision >= revision and not force_render:
            return {"ok": True, "updated": False, "delivery": delivery}

        edited = await update_progress_card(title, delivery)
        if edited.get("ok") is not True:
            await _record_card_update_failure(
                client,
                assignment_id=assignment_id,
                delivery=delivery,
                edited=edited,
            )
            return {"ok": False, "delivery": delivery, "card_update": edited}
        marked = await client.call_tool(
            "assignment_delivery",
            {
                "action": "mark_card_rendered",
                "assignment_id": assignment_id,
                "payload": {"expected_revision": revision},
            },
            retryable=False,
        )
        marked_delivery = result_object(marked)
        if marked_delivery is not None:
            return {
                "ok": True,
                "updated": True,
                "delivery": marked_delivery,
                "card_update": edited,
            }
        if not _is_revision_conflict(marked):
            return marked
        force_render = True
    return _invalid_delivery("Assignment delivery changed too frequently", retryable=True)


async def _record_card_update_failure(
    client: Any,
    *,
    assignment_id: str,
    delivery: dict[str, Any],
    edited: dict[str, Any],
) -> None:
    revision = delivery.get("revision")
    if not isinstance(revision, int):
        return
    error = edited.get("error")
    details = dict(error) if isinstance(error, dict) else {"code": "card_update_failed"}
    await client.call_tool(
        "assignment_delivery",
        {
            "action": "advance",
            "assignment_id": assignment_id,
            "payload": {
                "event": "card_update_failed",
                "expected_revision": revision,
                "error": details,
            },
        },
        retryable=False,
    )


def result_object(result: dict[str, Any]) -> dict[str, Any] | None:
    payload = result.get("result")
    return payload if isinstance(payload, dict) else None


def _is_revision_conflict(result: dict[str, Any]) -> bool:
    error = result.get("error")
    return (
        isinstance(error, dict)
        and error.get("code") == "invalid_request"
        and "revision conflict" in str(error.get("message") or "").lower()
    )


def _invalid_delivery(message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "assignment_delivery_invalid",
            "message": message,
            "retryable": retryable,
        },
    }


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
