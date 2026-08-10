from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from _assignment_delivery import parse_tool_result, progress_card
from _assignment_display import readable_name
from _assignment_display import resolve_feishu_display_names as _resolve_feishu_display_names
from _assignment_tool_common import CLIENT, dumps_result, invalid_argument
from _assignment_tool_common import result_object as _result_object
from _feishu_impl import get_users_batch_impl as _get_users_batch_impl
from feishu_message import feishu_message_send_card as _feishu_message_send_card

from psi_agent.session.runtime_context import get_session_id

_SESSION_PREFIX = "feishu-"
_OPEN_ID_RE = re.compile(r"ou_[A-Za-z0-9_]+")
_DELIVERABLE_STATES = {"assigned", "received", "plan_submitted", "in_progress"}


async def assignment_send_card(
    receive_id: str,
    assignment_id: str,
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send one authoritative assignment card to a recipient Feishu open_id.

    The current trusted Feishu Session must identify the assignment's assigner.
    Delivery claims are persisted before either the recipient card or the
    assigner's progress card is sent; reconciliation errors must not be retried.
    """
    normalized_receive_id = _required_text(receive_id)
    normalized_assignment_id = _required_text(assignment_id)
    if normalized_receive_id is None:
        return invalid_argument("receive_id must be a non-empty string")
    if normalized_assignment_id is None:
        return invalid_argument("assignment_id must be a non-empty string")
    if receive_id_type != "open_id":
        return invalid_argument("receive_id_type must be open_id")
    operator_open_id = _operator_open_id(get_session_id())
    if operator_open_id is None:
        return _error(
            "assignment_assigner_required",
            "Assignment delivery requires a trusted Feishu user Session",
        )

    fetched = await CLIENT.call_tool(
        "assignment_get",
        {"assignment_id": normalized_assignment_id},
        retryable=True,
    )
    if not fetched.get("ok"):
        return dumps_result(fetched)
    assignment = _result_object(fetched)
    if assignment is None:
        return invalid_argument("Fusion Memory returned an invalid assignment")
    if assignment.get("state") not in _DELIVERABLE_STATES:
        return dumps_result(
            {
                "ok": False,
                "error": {
                    "code": "assignment_state_invalid",
                    "message": "Only an active work arrangement can be delivered",
                    "retryable": False,
                },
            }
        )
    recipients = assignment.get("recipients")
    if not isinstance(recipients, list):
        return invalid_argument("assignment recipients must be an array")
    recipient_open_ids: set[str] = set()
    for participant in recipients:
        if not isinstance(participant, dict):
            continue
        raw_open_id = participant.get("feishu_open_id")
        if raw_open_id is not None and not isinstance(raw_open_id, str):
            return invalid_argument("assignment recipient feishu_open_id must be a string")
        open_id = _required_text(raw_open_id)
        if open_id is not None:
            recipient_open_ids.add(open_id)
        raw_aliases = participant.get("feishu_open_ids")
        if raw_aliases is not None and not isinstance(raw_aliases, list):
            return invalid_argument("assignment recipient feishu_open_ids must be an array")
        for raw_alias in raw_aliases or []:
            if not isinstance(raw_alias, str):
                return invalid_argument("assignment recipient feishu_open_ids items must be strings")
            alias = _required_text(raw_alias)
            if alias is not None:
                recipient_open_ids.add(alias)
    if normalized_receive_id not in recipient_open_ids:
        return invalid_argument("receive_id must identify an assignment recipient")

    if operator_open_id not in _participant_open_ids(assignment.get("assigner")):
        return _error(
            "assignment_assigner_required",
            "Only the assignment assigner may deliver this work",
        )

    participant_names = await _resolve_feishu_display_names(
        {operator_open_id, *recipient_open_ids},
        _get_users_batch_impl,
    )
    title = _required_text(assignment.get("title"))
    assigner_name = participant_names.get(operator_open_id) or "安排者"
    if title is None:
        return invalid_argument("assignment title is required")

    tracked = await CLIENT.call_tool(
        "assignment_delivery",
        {
            "action": "create",
            "assignment_id": normalized_assignment_id,
            "payload": {
                "assigner_open_id": operator_open_id,
                "read_deadline_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            },
        },
        retryable=False,
    )
    delivery = _result_object(tracked)
    if delivery is None:
        return dumps_result(tracked)

    recipient_claim = await _claim_send(
        normalized_assignment_id,
        target="recipient",
        recipient_open_id=normalized_receive_id,
    )
    claim_result = _result_object(recipient_claim)
    if claim_result is None:
        return dumps_result(recipient_claim)
    recipient_already_sent = False
    if claim_result.get("acquired") is not True:
        delivery = _result_object_field(claim_result, "delivery") or delivery
        recipient = _delivery_recipient(delivery, normalized_receive_id)
        if recipient is not None and recipient.get("send_status") == "sent":
            recipient_already_sent = True
        else:
            return _send_reconciliation_error("recipient", recipient)
    else:
        claim_token = _required_text(claim_result.get("claim_token"))
        if claim_token is None:
            return _error(
                "assignment_delivery_claim_invalid",
                "Fusion Memory returned an invalid recipient send claim",
            )

        card = _build_assignment_card(
            assignment=assignment,
            assignment_id=normalized_assignment_id,
            title=title,
            assigner_name=assigner_name,
            participant_names=participant_names,
        )
        business_context = {
            "type": "work_assignment",
            "assignment_id": normalized_assignment_id,
            "title": title,
            "assigner_name": assigner_name,
            "publish_target": "feishu_task",
        }
        action_handlers = {
            "confirm_assignment_receipt": "assignment_accept",
        }
        recipient_result = await _send_card(
            normalized_receive_id,
            json.dumps(card, ensure_ascii=False),
            receive_id_type,
            operator_open_id,
            json.dumps(business_context, ensure_ascii=False),
            json.dumps(action_handlers, ensure_ascii=False),
        )
        recipient_message_id = _required_text(recipient_result.get("message_id"))
        if recipient_result.get("ok") is not True or recipient_message_id is None:
            await _finalize_send(
                normalized_assignment_id,
                action="fail_send",
                target="recipient",
                claim_token=claim_token,
                recipient_open_id=normalized_receive_id,
                error=_send_error(recipient_result, recipient_message_id),
            )
            return dumps_result(
                {
                    "ok": False,
                    "sent": recipient_result.get("sent") is True,
                    "error": {
                        "code": "assignment_delivery_reconciliation_required",
                        "message": (
                            "Recipient card send failed or returned no message id; reconcile it before another send"
                        ),
                        "retryable": False,
                    },
                    "feishu_error": recipient_result.get("error"),
                }
            )
        completed = await _finalize_send(
            normalized_assignment_id,
            action="complete_send",
            target="recipient",
            claim_token=claim_token,
            recipient_open_id=normalized_receive_id,
            message_id=recipient_message_id,
        )
        delivery = _result_object(completed)
        if delivery is None:
            return _partial_send_error(
                completed,
                reason="recipient card was sent but its delivery could not be finalized",
            )

    progress_claim = await _claim_send(
        normalized_assignment_id,
        target="progress",
    )
    progress_claim_result = _result_object(progress_claim)
    if progress_claim_result is None:
        return _partial_send_error(
            progress_claim,
            reason="recipient card is tracked but progress-card claim failed",
        )
    if progress_claim_result.get("acquired") is not True:
        delivery = _result_object_field(progress_claim_result, "delivery") or delivery
        if delivery.get("progress_status") != "sent":
            return _send_reconciliation_error("progress", None, sent=True)
        return dumps_result(
            {
                "ok": True,
                "sent": True,
                "already_sent": recipient_already_sent,
                "assignment_id": normalized_assignment_id,
                "delivery_tracking": {
                    "tracked": True,
                    "progress_message_id": delivery.get("assigner_progress_message_id"),
                },
            }
        )

    progress_claim_token = _required_text(progress_claim_result.get("claim_token"))
    delivery = _result_object_field(progress_claim_result, "delivery") or delivery
    if progress_claim_token is None:
        return _error(
            "assignment_delivery_claim_invalid",
            "Fusion Memory returned an invalid progress-card send claim",
        )
    progress_result = await _send_card(
        operator_open_id,
        json.dumps(progress_card(title, delivery), ensure_ascii=False),
        "open_id",
        operator_open_id,
    )
    progress_message_id = _required_text(progress_result.get("message_id"))
    if progress_result.get("ok") is not True or progress_message_id is None:
        await _finalize_send(
            normalized_assignment_id,
            action="fail_send",
            target="progress",
            claim_token=progress_claim_token,
            error=_send_error(progress_result, progress_message_id),
        )
        return _partial_send_error(
            progress_result,
            reason="recipient card is tracked but progress card could not be sent",
        )
    progress_completed = await _finalize_send(
        normalized_assignment_id,
        action="complete_send",
        target="progress",
        claim_token=progress_claim_token,
        message_id=progress_message_id,
    )
    if _result_object(progress_completed) is None:
        return _partial_send_error(
            progress_completed,
            reason="progress card was sent but its delivery could not be finalized",
        )
    return dumps_result(
        {
            "ok": True,
            "sent": True,
            "assignment_id": normalized_assignment_id,
            "delivery_tracking": {
                "tracked": True,
                "progress_message_id": progress_message_id,
            },
        }
    )


def _build_assignment_card(
    *,
    assignment: dict[str, Any],
    assignment_id: str,
    title: str,
    assigner_name: str,
    participant_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    safe_assigner_name = readable_name(assigner_name) or "安排者"
    elements: list[dict[str, Any]] = [
        _plain_text_element(f"任务: {title}\n安排者: {safe_assigner_name}"),
    ]
    original_request = assignment.get("original_request")
    if isinstance(original_request, str) and original_request.strip():
        elements.extend(
            [
                _heading_element("安排者原始内容 (原文或语音转写, 未改写)"),
                _plain_text_element(original_request),
            ]
        )
    analysis: list[str] = []
    _append_labeled_text(analysis, "背景", assignment.get("context"))
    _append_labeled_text(analysis, "期望结果", assignment.get("expected_outcome"))
    _append_items(analysis, "待确认缺口", assignment.get("gaps"), participant_names)
    _append_items(analysis, "已识别风险", assignment.get("risks"), participant_names)
    _append_items(analysis, "行动项", assignment.get("action_items"), participant_names)
    if analysis:
        elements.extend(
            [
                _heading_element("Agent 分析整理 (非安排者原话)"),
                _plain_text_element("\n".join(analysis)),
            ]
        )
    sources = _source_texts(assignment.get("evidence_refs"))
    if sources:
        elements.extend(
            [
                _heading_element("参考资料"),
                _plain_text_element("\n".join(sources)),
            ]
        )
    elements.append(
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "确认接收并创建飞书任务"},
                    "type": "primary",
                    "value": {"action": "confirm_assignment_receipt", "assignment_id": assignment_id},
                }
            ],
        }
    )
    elements.append(
        _plain_text_element(
            "遇到任务范围、截止时间、验收标准、资源或权限等无法自行确认的问题。"
            "可以直接告诉 HaiTun。反馈会保留在本任务中并同步给安排者。"
        )
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "新的工作安排"},
            "template": "blue",
        },
        "elements": elements,
    }


def _participant_open_ids(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    open_ids: set[str] = set()
    for field in ("feishu_open_id", "open_id", "delivery_open_id"):
        if text := _optional_text(value.get(field)):
            open_ids.add(text)
    for field in ("feishu_open_ids", "open_ids"):
        aliases = value.get(field)
        if isinstance(aliases, list):
            open_ids.update(text for item in aliases if (text := _optional_text(item)))
    return open_ids


def _delivery_recipient(delivery: dict[str, Any], open_id: str) -> dict[str, Any] | None:
    recipients = delivery.get("recipients")
    if not isinstance(recipients, list):
        return None
    return next(
        (
            recipient
            for recipient in recipients
            if isinstance(recipient, dict) and open_id in _participant_open_ids(recipient)
        ),
        None,
    )


async def _send_card(*args: Any) -> dict[str, Any]:
    try:
        return parse_tool_result(await _feishu_message_send_card(*args))
    except Exception as exc:
        return {
            "ok": False,
            "sent": False,
            "error": {
                "code": "feishu_card_send_failed",
                "message": str(exc),
            },
        }


async def _claim_send(
    assignment_id: str,
    *,
    target: str,
    recipient_open_id: str = "",
) -> dict[str, Any]:
    payload = {"target": target}
    if recipient_open_id:
        payload["recipient_open_id"] = recipient_open_id
    return await CLIENT.call_tool(
        "assignment_delivery",
        {
            "action": "claim_send",
            "assignment_id": assignment_id,
            "payload": payload,
        },
        retryable=False,
    )


async def _finalize_send(
    assignment_id: str,
    *,
    action: str,
    target: str,
    claim_token: str,
    recipient_open_id: str = "",
    message_id: str = "",
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target": target,
        "claim_token": claim_token,
    }
    if recipient_open_id:
        payload["recipient_open_id"] = recipient_open_id
    if message_id:
        payload["message_id"] = message_id
    if error is not None:
        payload["error"] = error
    return await CLIENT.call_tool(
        "assignment_delivery",
        {
            "action": action,
            "assignment_id": assignment_id,
            "payload": payload,
        },
        retryable=False,
    )


def _send_error(result: dict[str, Any], message_id: str | None) -> dict[str, Any]:
    error = result.get("error")
    if isinstance(error, dict):
        return dict(error)
    return {
        "code": "assignment_message_id_missing"
        if result.get("ok") is True and message_id is None
        else "assignment_card_send_failed",
        "sent": result.get("sent") is True,
    }


def _send_reconciliation_error(
    target: str,
    recipient: dict[str, Any] | None,
    *,
    sent: bool = False,
) -> str:
    status = recipient.get("send_status") if recipient is not None else None
    return _error(
        "assignment_delivery_reconciliation_required",
        f"The {target} card send is {status or 'already claimed or failed'}; reconcile it before another send",
        sent=sent,
    )


def _partial_send_error(result: dict[str, Any], *, reason: str) -> str:
    return dumps_result(
        {
            "ok": False,
            "sent": True,
            "error": {
                "code": "assignment_delivery_reconciliation_required",
                "message": reason,
                "retryable": False,
            },
            "memory_error": result.get("error"),
        }
    )


def _error(code: str, message: str, *, sent: bool = False) -> str:
    return dumps_result(
        {
            "ok": False,
            "sent": sent,
            "error": {"code": code, "message": message, "retryable": False},
        }
    )


def _result_object_field(value: dict[str, Any], field: str) -> dict[str, Any] | None:
    payload = value.get(field)
    return payload if isinstance(payload, dict) else None


def _operator_open_id(session_id: str | None) -> str | None:
    if not isinstance(session_id, str) or not session_id.startswith(_SESSION_PREFIX):
        return None
    candidate = session_id[len(_SESSION_PREFIX) :]
    return candidate if _OPEN_ID_RE.fullmatch(candidate) else None


def _plain_text_element(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "plain_text", "content": content}}


def _heading_element(label: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": f"**{label}**"}


def _append_labeled_text(lines: list[str], label: str, value: Any) -> None:
    text = _optional_text(value)
    if text is not None:
        lines.append(f"{label}: {text}")


def _append_items(
    lines: list[str],
    label: str,
    value: Any,
    participant_names: dict[str, str] | None,
) -> None:
    if not isinstance(value, list):
        return
    normalized = [text for item in value if (text := _item_text(item, participant_names))]
    lines.extend(f"{label}: {item}" for item in normalized)


def _source_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    sources: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        uri = _optional_text(item.get("uri")) or _optional_text(item.get("url"))
        if uri:
            sources.append(uri)
    return sources


def _item_text(value: Any, participant_names: dict[str, str] | None) -> str | None:
    if not isinstance(value, dict):
        return None
    content = None
    for field in ("description", "action", "title", "name"):
        text = _optional_text(value.get(field))
        if text:
            content = text
            break
    if content is None:
        return None
    details: list[str] = []
    owner = _first_participant_name(
        value,
        ("owner", "responsible", "assignee"),
        participant_names,
    )
    if owner is not None:
        details.append(f"负责人: {owner}")
    deadline = _first_text(value, ("deadline", "due", "due_at"))
    if deadline is not None:
        details.append(f"截止时间: {deadline}")
    status = _optional_text(value.get("status"))
    if status is not None:
        details.append(f"状态: {status}")
    return " | ".join([content, *details])


def _first_participant_name(
    value: dict[str, Any],
    fields: tuple[str, ...],
    participant_names: dict[str, str] | None,
) -> str | None:
    if not participant_names:
        return None
    for field in fields:
        participant = value.get(field)
        open_ids = {participant} if isinstance(participant, str) else _participant_open_ids(participant)
        for open_id in sorted(open_ids):
            if name := participant_names.get(open_id):
                return name
    return None


def _first_text(value: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    for field in fields:
        text = _optional_text(value.get(field))
        if text is not None:
            return text
    return None


def _required_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_text(value: Any) -> str | None:
    return _required_text(value)
