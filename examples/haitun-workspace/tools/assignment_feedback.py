from __future__ import annotations

import json
import re
from typing import Any

from _assignment_display import resolve_feishu_display_name as _resolve_feishu_display_name
from _assignment_tool_common import (
    CLIENT,
    dumps_result,
    invalid_argument,
    parse_json_object,
    result_object,
)
from _feishu_impl import edit_card_impl as _edit_card_impl
from _feishu_impl import get_users_batch_impl as _get_users_batch_impl
from _feishu_impl import send_card_impl as _send_card_impl

from psi_agent.session.runtime_context import get_session_id

_CALLBACK_HANDLER = "assignment_feedback"
_REPLY_ACTION = "assignment_feedback_reply"
_CONFIRM_ACTION = "assignment_feedback_confirm"
_CUSTOM_REPLY_FIELD = "custom_reply"
_CUSTOM_OPTION_VALUES = {"custom", "custom_time", "other", "其他", "其他时间", "其他答复"}
_SUPPORTED_ACTIONS = {"create", "append", "assigner_reply", "recipient_confirm"}
_ENTRY_TYPES = {"question", "reply", "confirm", "private_note"}
_AUTHOR_ROLES = {"recipient", "assigner", "agent", "system"}
_NOTIFICATION_STRATEGIES = {"blocking", "non_blocking", "record_only"}
_PROJECTION_TEXT_FIELDS = (
    "assignment_title",
    "stage",
    "missing_information",
    "why_blocked",
    "impact",
    "updated_understanding",
    "plan_delta",
)
_ROLE_LABELS = {
    "assigner": "安排者",
    "recipient": "接收者",
    "agent": "Agent",
    "system": "系统",
}
_HUMAN_AUTHOR_ROLES = {"assigner", "recipient"}
_SESSION_PREFIX = "feishu-"
_OPEN_ID_RE = re.compile(r"ou_[A-Za-z0-9_]+")


async def assignment_feedback(
    receive_id: str = "",
    arrangement_id: str = "",
    action: str = "",
    payload_json: str = "{}",
    receive_id_type: str = "open_id",
    card_action_json: str = "",
) -> str:
    """Record one shared assignment feedback thread and maintain its Feishu card projections.

    Use one of two modes:

    1. New or programmatic feedback: pass ``receive_id``, ``arrangement_id``,
       ``action`` and ``payload_json``. The only supported actions are
       ``create``, ``append``, ``assigner_reply`` and ``recipient_confirm``.
       Card binding is internal and must not be called directly. ``payload_json``
       is a JSON object, never a JSON string nested inside another object. Use
       these exact shapes:

       ``action="create"`` or ``action="append"`` payload:
       ``{"raw_content": "请确认截止时间", "author_role": "recipient", "entry_type": "question",``
       ``"notification_strategy": "blocking", "attempts": ["已核查内容"]}``
       Option item shape: ``"options": [{"label": "选项 A", "value": "option_a", "recommended": true}]``.

       ``action="assigner_reply"``:
       ``{"raw_content":"下周五", "author_role":"assigner", "entry_type":"reply", "notification_strategy":"blocking"}``

       ``action="recipient_confirm"``:
       ``{"raw_content": "已确认更新后的理解", "author_role": "recipient", "entry_type": "confirm",``
       ``"notification_strategy": "record_only"}``

       ``notification_strategy`` is ``blocking``, ``non_blocking`` or
       ``record_only``. ``attempts`` must be an array of strings. A blocking
       entry needs 2-3 concrete ``options``; do not use an ``other`` option because
       the card always provides a separate custom-reply input. Each option has the
       shape ``"options": [{"label": "选项 A", "value": "option_a", "recommended": true}]``.
       The tool rejects
       unknown actions or malformed payloads before contacting Memory. The card's
       task title and each entry's author name are resolved from the authoritative
       assignment record behind ``arrangement_id``; do not guess or pass them.
    2. Feishu card callback: when the latest user message contains
       ``<feishu_card_action>`` with ``dispatch.handler="assignment_feedback"``,
       pass the entire JSON object as ``card_action_json`` and omit every other
       argument. The tool validates the operator, extracts a quick option,
       ``form_value.custom_reply``, or recipient confirmation, and writes a
       correctly typed ``assigner_reply`` or ``recipient_confirm``. An assigner
       reply updates the assigner card and sends the original feedback author a
       recipient result card for confirmation. Do not call tool discovery, read
       source files, or send a separate message after a successful callback.
    """
    callback_mode = bool(_required_text(card_action_json))
    callback: dict[str, Any] | None = None
    if callback_mode:
        callback, callback_error = _parse_feedback_card_action(card_action_json)
        if callback_error is not None or callback is None:
            return invalid_argument(callback_error or "card_action_json is invalid")
        receive_id = str(callback["receive_id"])
        arrangement_id = str(callback["arrangement_id"])
        action = str(callback["action"])
        user_key = str(callback["operator_open_id"])
    else:
        user_key = _operator_open_id(get_session_id()) or ""

    normalized_receive_id = _required_text(receive_id)
    normalized_arrangement_id = _required_text(arrangement_id)
    normalized_action = _required_text(action)
    if normalized_receive_id is None:
        return invalid_argument("receive_id must be a non-empty string")
    if normalized_arrangement_id is None:
        return invalid_argument("arrangement_id must be a non-empty string")
    if normalized_action is None:
        return invalid_argument("action must be a non-empty string")
    if normalized_action not in _SUPPORTED_ACTIONS:
        return invalid_argument("action must be one of create, append, assigner_reply, or recipient_confirm")
    payload, error = parse_json_object(payload_json, "payload_json")
    if error is not None or payload is None:
        return invalid_argument(error or "payload_json must be a JSON object")
    if callback is not None:
        payload.update(callback["payload"])
    payload_error = _validate_payload(normalized_action, payload)
    if payload_error is not None:
        return invalid_argument(payload_error)
    if (
        normalized_action in {"create", "append"}
        and _required_text(payload.get("notification_strategy")) == "blocking"
        and _required_text(payload.get("entry_type")) != "private_note"
        and not 2 <= _concrete_option_count(payload) <= 3
    ):
        return invalid_argument("blocking feedback requires 2-3 concrete options")

    directory = await _assignment_directory(normalized_arrangement_id)
    for field in ("author_display_name", "author_name", "author_open_id"):
        payload.pop(field, None)
    if title := _required_text(directory.get("assignment_title")):
        payload["assignment_title"] = title
    else:
        payload.pop("assignment_title", None)

    managed = await CLIENT.call_tool(
        "assignment_feedback",
        {
            "arrangement_id": normalized_arrangement_id,
            "action": normalized_action,
            "payload": payload,
        },
        retryable=False,
    )
    if not managed.get("ok"):
        return dumps_result(managed)
    thread = managed.get("result")
    if not isinstance(thread, dict):
        return invalid_argument("Fusion Memory returned an invalid feedback thread")

    if payload.get("entry_type") == "private_note":
        return dumps_result(_success_result(thread, notified=False, callback_handled=callback_mode))

    thread_card_id = _required_text(thread.get("card_id"))
    callback_card_id = _required_text(callback.get("message_id")) if callback is not None else None
    recipient_confirmation = callback is not None and callback["action"] == "recipient_confirm"
    if (
        callback_card_id is not None
        and thread_card_id is not None
        and callback_card_id != thread_card_id
        and not recipient_confirmation
    ):
        return invalid_argument("card action message_id does not match the feedback thread card")
    if callback_mode and thread_card_id is None and callback_card_id is None:
        return dumps_result(
            {
                "ok": False,
                "feedback_saved": True,
                "callback_handled": True,
                "arrangement_id": normalized_arrangement_id,
                "state": thread.get("state"),
                "error": {
                    "code": "feedback_card_binding_missing",
                    "message": "Feedback callback was saved but the consumed card id is unavailable",
                    "retryable": False,
                },
            }
        )
    card_id = thread_card_id or callback_card_id
    callback_binding_required = (
        callback_mode and not recipient_confirmation and thread_card_id is None and callback_card_id is not None
    )
    strategy = _required_text(payload.get("notification_strategy")) or "record_only"
    recipient_ack_required = not callback_mode and payload.get("author_role") == "recipient" and strategy == "blocking"
    if card_id is None and strategy != "blocking":
        return dumps_result(_success_result(thread, notified=False, callback_handled=callback_mode))

    card_json = json.dumps(
        _build_feedback_card(
            arrangement_id=normalized_arrangement_id,
            thread=thread,
            payload=payload,
            directory=directory,
        ),
        ensure_ascii=False,
    )
    if card_id is not None:
        edited = await _edit_card_impl(card_id, card_json, user_key)
        if not edited.get("ok"):
            return dumps_result(
                {
                    **edited,
                    "feedback_saved": True,
                    "arrangement_id": normalized_arrangement_id,
                    "state": thread.get("state"),
                    "card_id": card_id,
                }
            )
        if callback_binding_required:
            bound = await CLIENT.call_tool(
                "assignment_feedback",
                {
                    "arrangement_id": normalized_arrangement_id,
                    "action": "bind_card",
                    "payload": {"card_id": card_id},
                },
                retryable=False,
            )
            if not bound.get("ok"):
                return dumps_result(
                    {
                        "ok": False,
                        "feedback_saved": True,
                        "callback_handled": True,
                        "card_updated": True,
                        "card_id": card_id,
                        "error": bound.get("error")
                        or {
                            "code": "feedback_card_binding_failed",
                            "message": "Feedback card was updated but its Memory binding could not be saved",
                            "retryable": False,
                        },
                    }
                )
            bound_thread = bound.get("result")
            if isinstance(bound_thread, dict):
                thread = bound_thread
        if recipient_confirmation and callback_card_id is not None and callback_card_id != card_id:
            recipient_edited = await _edit_card_impl(callback_card_id, card_json, user_key)
            if not recipient_edited.get("ok"):
                return dumps_result(
                    {
                        **recipient_edited,
                        "feedback_saved": True,
                        "assigner_card_updated": True,
                        "recipient_card_updated": False,
                        "arrangement_id": normalized_arrangement_id,
                        "state": thread.get("state"),
                    }
                )
        if callback is not None and callback["action"] == "assigner_reply":
            recipient_open_id = _required_text(callback.get("recipient_open_id"))
            if recipient_open_id is not None:
                recipient_card_json = json.dumps(
                    _build_feedback_card(
                        arrangement_id=normalized_arrangement_id,
                        thread=thread,
                        payload=payload,
                        directory=directory,
                        recipient_view=True,
                    ),
                    ensure_ascii=False,
                )
                recipient_sent = await _send_card_impl(
                    recipient_open_id,
                    recipient_card_json,
                    "open_id",
                    user_key,
                    json.dumps(
                        {
                            "type": "assignment_feedback_recipient_result",
                            "arrangement_id": normalized_arrangement_id,
                            "thread_id": thread.get("thread_id"),
                            "recipient_open_id": recipient_open_id,
                            "stage": payload.get("stage"),
                            "projection": _feedback_projection(payload),
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({_CONFIRM_ACTION: _CALLBACK_HANDLER}, ensure_ascii=False),
                )
                if not recipient_sent.get("ok"):
                    return dumps_result(
                        {
                            **recipient_sent,
                            "feedback_saved": True,
                            "card_updated": True,
                            "recipient_notified": False,
                            "arrangement_id": normalized_arrangement_id,
                            "state": thread.get("state"),
                        }
                    )
                recipient_card_id = _required_text(recipient_sent.get("message_id"))
                if recipient_card_id is None:
                    return dumps_result(
                        {
                            "ok": False,
                            "feedback_saved": True,
                            "card_updated": True,
                            "recipient_notified": False,
                            "arrangement_id": normalized_arrangement_id,
                            "state": thread.get("state"),
                            "error": {
                                "code": "feedback_recipient_card_id_missing",
                                "message": "Feishu sent the recipient feedback card without returning a message_id",
                                "retryable": False,
                            },
                        }
                    )
                return dumps_result(
                    _success_result(
                        thread,
                        notified=True,
                        card_id=card_id,
                        card_updated=True,
                        callback_handled=True,
                        recipient_notified=True,
                        recipient_card_id=recipient_card_id,
                    )
                )
        return dumps_result(
            _success_result(
                thread,
                notified=True,
                card_id=card_id,
                card_updated=True,
                callback_handled=callback_mode,
                assistant_reply_required=recipient_ack_required,
                recipient_notified=(False if callback is not None and callback["action"] == "assigner_reply" else None),
            )
        )

    sent = await _send_card_impl(
        normalized_receive_id,
        card_json,
        receive_id_type,
        user_key,
        json.dumps(
            {
                "type": "assignment_feedback",
                "arrangement_id": normalized_arrangement_id,
                "thread_id": thread.get("thread_id"),
                "reply_target_open_id": normalized_receive_id,
                "stage": payload.get("stage"),
                "projection": _feedback_projection(payload),
            },
            ensure_ascii=False,
        ),
        json.dumps(_action_handlers(thread, payload), ensure_ascii=False),
    )
    if not sent.get("ok"):
        return dumps_result(
            {
                **sent,
                "feedback_saved": True,
                "arrangement_id": normalized_arrangement_id,
                "state": thread.get("state"),
            }
        )
    message_id = _required_text(sent.get("message_id"))
    if message_id is None:
        return dumps_result(
            {
                "ok": False,
                "feedback_saved": True,
                "sent": True,
                "error": {
                    "code": "feedback_card_id_missing",
                    "message": "Feishu sent the feedback card without returning a message_id",
                    "retryable": False,
                },
            }
        )

    bound = await CLIENT.call_tool(
        "assignment_feedback",
        {
            "arrangement_id": normalized_arrangement_id,
            "action": "bind_card",
            "payload": {"card_id": message_id},
        },
        retryable=False,
    )
    if not bound.get("ok"):
        return dumps_result(
            {
                "ok": False,
                "feedback_saved": True,
                "sent": True,
                "card_id": message_id,
                "card_binding_saved": False,
                "error": bound.get("error")
                or {
                    "code": "feedback_card_binding_failed",
                    "message": "The feedback card was sent but could not be bound to its thread",
                    "retryable": False,
                },
            }
        )
    bound_thread = bound.get("result")
    if not isinstance(bound_thread, dict):
        bound_thread = {**thread, "card_id": message_id}
    return dumps_result(
        _success_result(
            bound_thread,
            notified=True,
            card_id=message_id,
            card_updated=False,
            callback_handled=callback_mode,
            assistant_reply_required=recipient_ack_required,
        )
    )


def _build_feedback_card(
    *,
    arrangement_id: str,
    thread: dict[str, Any],
    payload: dict[str, Any],
    directory: dict[str, Any] | None = None,
    recipient_view: bool = False,
) -> dict[str, Any]:
    state = _required_text(thread.get("state")) or "open"
    assignment_title = _required_text(_dict_value(directory).get("assignment_title")) or "当前工作安排"
    elements: list[dict[str, Any]] = [
        _plain_text_element(
            "\n".join(
                part
                for part in (
                    f"所属任务: {assignment_title}",
                    _labeled_text("当前阶段", payload.get("stage")),
                    f"状态: {_state_label(state)}",
                )
                if part is not None
            )
        )
    ]
    shared_entries = _shared_entry_lines(thread, directory=directory)
    if not shared_entries:
        raw_content = _required_text(payload.get("raw_content"))
        if raw_content is not None:
            shared_entries = [f"1. {raw_content}"]
    if shared_entries:
        elements.extend(
            [
                _heading_element("共享反馈记录 (原文, 未改写)"),
                _plain_text_element("\n\n".join(shared_entries)),
            ]
        )

    analysis = _analysis_lines(payload)
    if analysis:
        elements.extend(
            [
                _heading_element("Agent 分析 (非原始反馈)"),
                _plain_text_element("\n".join(analysis)),
            ]
        )

    actions = _card_actions(arrangement_id=arrangement_id, state=state, payload=payload)
    if actions:
        elements.append({"tag": "action", "actions": actions})
    if state in {"open", "blocked"}:
        elements.append(_custom_reply_form(arrangement_id))
    if recipient_view and state == "updated_waiting_recipient_confirmation":
        elements.append(
            {
                "tag": "action",
                "actions": [
                    _button(
                        "确认更新后的理解",
                        {
                            "action": _CONFIRM_ACTION,
                            "arrangement_id": arrangement_id,
                            "feedback_action": "recipient_confirm",
                        },
                        primary=True,
                    )
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "工作安排反馈"},
            "template": "orange" if state in {"open", "blocked"} else "blue",
        },
        "elements": elements,
    }


def _analysis_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for label, field in (
        ("缺少的信息", "missing_information"),
        ("无法自行推断的原因", "why_blocked"),
        ("进度影响", "impact"),
        ("更新后的任务理解", "updated_understanding"),
        ("方案变化与恢复点", "plan_delta"),
    ):
        value = _required_text(payload.get(field))
        if value is not None:
            lines.append(f"{label}: {value}")
    attempts = _string_items(payload.get("attempts"))
    lines.extend(f"已核查或尝试: {item}" for item in attempts)
    return lines


def _card_actions(*, arrangement_id: str, state: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if state not in {"open", "blocked"}:
        return []
    actions: list[dict[str, Any]] = []
    for option in _valid_options(payload):
        label = str(option["label"])
        value = str(option["value"])
        actions.append(
            _button(
                label,
                {
                    "action": "assignment_feedback_reply",
                    "arrangement_id": arrangement_id,
                    "feedback_action": "assigner_reply",
                    "selected_value": value,
                    "selected_label": label,
                },
                primary=option.get("recommended") is True,
            )
        )
    return actions


def _action_handlers(thread: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    state = _required_text(thread.get("state")) or "open"
    handlers: dict[str, str] = {}
    if state in {"open", "blocked"}:
        handlers[_REPLY_ACTION] = _CALLBACK_HANDLER
    return handlers


def _custom_reply_form(arrangement_id: str) -> dict[str, Any]:
    return {
        "tag": "form",
        "name": "assignment_feedback_reply_form",
        "elements": [
            {
                "tag": "input",
                "name": _CUSTOM_REPLY_FIELD,
                "required": True,
                "placeholder": {"tag": "plain_text", "content": "输入其他答复"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "提交答复"},
                "type": "primary",
                "name": "submit_reply",
                "action_type": "form_submit",
                "value": {
                    "action": _REPLY_ACTION,
                    "arrangement_id": arrangement_id,
                    "feedback_action": "assigner_reply",
                },
            },
        ],
    }


def _valid_options(payload: dict[str, Any]) -> list[dict[str, Any]]:
    options = payload.get("options")
    if not isinstance(options, list):
        return []
    valid: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict) or option.get("custom") is True:
            continue
        label = _required_text(option.get("label"))
        value = _required_text(option.get("value"))
        if label is None or value is None:
            continue
        if label.casefold() in _CUSTOM_OPTION_VALUES or value.casefold() in _CUSTOM_OPTION_VALUES:
            continue
        valid.append({**option, "label": label, "value": value})
        if len(valid) == 3:
            break
    return valid


def _concrete_option_count(payload: dict[str, Any]) -> int:
    options = payload.get("options")
    if not isinstance(options, list):
        return 0
    count = 0
    for option in options:
        if not isinstance(option, dict) or option.get("custom") is True:
            continue
        label = _required_text(option.get("label"))
        value = _required_text(option.get("value"))
        if label is None or value is None:
            continue
        if label.casefold() in _CUSTOM_OPTION_VALUES or value.casefold() in _CUSTOM_OPTION_VALUES:
            continue
        count += 1
    return count


def _validate_payload(action: str, payload: dict[str, Any]) -> str | None:
    if action not in _SUPPORTED_ACTIONS:
        return None

    author_role = payload.get("author_role")
    if author_role not in _AUTHOR_ROLES:
        return "payload_json.author_role is required for feedback entries"

    entry_type = payload.get("entry_type")
    if entry_type not in _ENTRY_TYPES:
        return "payload_json.entry_type must be question, reply, confirm, or private_note"

    strategy = payload.get("notification_strategy")
    if strategy not in _NOTIFICATION_STRATEGIES:
        return "payload_json.notification_strategy must be blocking, non_blocking, or record_only"

    if action == "assigner_reply" and (author_role != "assigner" or entry_type != "reply"):
        return 'payload_json.author_role and entry_type must be "assigner" and "reply" for assigner_reply'
    if action == "recipient_confirm" and (author_role != "recipient" or entry_type != "confirm"):
        return 'payload_json.author_role and entry_type must be "recipient" and "confirm" for recipient_confirm'
    if entry_type == "private_note" and strategy != "record_only":
        return 'payload_json.notification_strategy must be "record_only" for private_note'

    attempts = payload.get("attempts")
    if attempts is not None and (
        not isinstance(attempts, list) or any(not isinstance(item, str) or not item.strip() for item in attempts)
    ):
        return "payload_json.attempts must be an array of strings"

    options = payload.get("options")
    if options is not None:
        if not isinstance(options, list):
            return "payload_json.options must be an array of objects"
        for option in options:
            if not isinstance(option, dict):
                return "payload_json.options must be an array of objects"
            if option.get("custom") is True:
                return "payload_json.options must not include custom=true"
            if not _required_text(option.get("label")) or not _required_text(option.get("value")):
                return "payload_json.options items require non-empty label and value"
            recommended = option.get("recommended")
            if recommended is not None and not isinstance(recommended, bool):
                return "payload_json.options[].recommended must be a boolean"
            label = _required_text(option.get("label")) or ""
            value = _required_text(option.get("value")) or ""
            if label.casefold() in _CUSTOM_OPTION_VALUES or value.casefold() in _CUSTOM_OPTION_VALUES:
                return "payload_json.options must not include reserved custom option"

    if (
        action in {"create", "append", "assigner_reply", "recipient_confirm"}
        and _required_text(payload.get("raw_content")) is None
    ):
        return "payload_json.raw_content is required for feedback entries"
    return None


def _button(label: str, value: dict[str, str], *, primary: bool) -> dict[str, Any]:
    button: dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "value": value,
    }
    if primary:
        button["type"] = "primary"
    return button


def _success_result(
    thread: dict[str, Any],
    *,
    notified: bool,
    card_id: str | None = None,
    card_updated: bool | None = None,
    callback_handled: bool = False,
    assistant_reply_required: bool = False,
    recipient_notified: bool | None = None,
    recipient_card_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "feedback_saved": True,
        "notified": notified,
        "arrangement_id": thread.get("arrangement_id"),
        "thread_id": thread.get("thread_id"),
        "state": thread.get("state"),
        "version": thread.get("version"),
        "card_id": card_id or thread.get("card_id"),
    }
    if card_updated is not None:
        result["card_updated"] = card_updated
    if recipient_notified is not None:
        result["recipient_notified"] = recipient_notified
    if recipient_card_id is not None:
        result["recipient_card_id"] = recipient_card_id
    if callback_handled:
        result["callback_handled"] = True
        result["assistant_reply_required"] = False
    elif assistant_reply_required:
        result["assistant_reply_required"] = True
        result["assistant_reply"] = "已提交反馈, 等待安排者处理"
    return result


def _parse_feedback_card_action(card_action_json: str) -> tuple[dict[str, Any] | None, str | None]:
    envelope, error = parse_json_object(card_action_json, "card_action_json")
    if error is not None or envelope is None:
        return None, error or "card_action_json must be a JSON object"

    dispatch = _dict_value(envelope.get("dispatch"))
    if dispatch.get("matched") is not True or _required_text(dispatch.get("handler")) != _CALLBACK_HANDLER:
        return None, "card action is not matched to assignment_feedback"

    action_data = _dict_value(envelope.get("action"))
    value = _dict_value(action_data.get("value"))
    action_name = _required_text(value.get("action"))
    feedback_action = _required_text(value.get("feedback_action"))
    if (action_name, feedback_action) not in {
        (_REPLY_ACTION, "assigner_reply"),
        (_CONFIRM_ACTION, "recipient_confirm"),
    }:
        return None, "card action is not a supported assignment feedback action"

    business = _dict_value(envelope.get("business_context"))
    arrangement_id = _required_text(value.get("arrangement_id")) or _required_text(business.get("arrangement_id"))
    business_arrangement_id = _required_text(business.get("arrangement_id"))
    if arrangement_id is None:
        return None, "card action is missing arrangement_id"
    if business_arrangement_id is not None and business_arrangement_id != arrangement_id:
        return None, "card action arrangement_id does not match business context"

    source = _dict_value(envelope.get("source"))
    operator_open_id = _required_text(source.get("operator_open_id")) or _required_text(
        envelope.get("operator_open_id")
    )
    expected_operator_open_id = (
        _required_text(business.get("recipient_open_id"))
        if feedback_action == "recipient_confirm"
        else _required_text(business.get("reply_target_open_id"))
    )
    if operator_open_id is None or expected_operator_open_id is None:
        return None, "card action is missing operator or target identity"
    if operator_open_id != expected_operator_open_id:
        return None, "only the feedback action target may submit this action"

    if feedback_action == "recipient_confirm":
        raw_content = "已确认更新后的理解"
        author_role = "recipient"
        entry_type = "confirm"
        notification_strategy = "record_only"
    else:
        form_value = _dict_value(action_data.get("form_value"))
        raw_content = _required_text(form_value.get(_CUSTOM_REPLY_FIELD))
        if raw_content is None:
            raw_content = _required_text(value.get("selected_label")) or _required_text(value.get("selected_value"))
        if raw_content is None or raw_content.casefold() in _CUSTOM_OPTION_VALUES:
            return None, "enter a non-empty custom reply or select a concrete option"
        author_role = "assigner"
        entry_type = "reply"
        notification_strategy = "blocking"

    payload = _feedback_projection(_dict_value(business.get("projection")))
    stage = _required_text(business.get("stage")) or _required_text(payload.get("stage"))
    payload.update(
        {
            "raw_content": raw_content,
            "author_role": author_role,
            "entry_type": entry_type,
            "notification_strategy": notification_strategy,
        }
    )
    if feedback_action == "assigner_reply":
        payload["updated_understanding"] = f"安排者已补充: {raw_content}"
    if stage is not None:
        payload["stage"] = stage
    return {
        "message_id": _required_text(envelope.get("message_id")),
        "receive_id": expected_operator_open_id,
        "arrangement_id": arrangement_id,
        "action": feedback_action,
        "operator_open_id": operator_open_id,
        "recipient_open_id": (
            _required_text(source.get("sender_open_id"))
            if feedback_action == "assigner_reply"
            else expected_operator_open_id
        ),
        "payload": payload,
    }, None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _assignment_directory(arrangement_id: str) -> dict[str, Any]:
    """Best-effort identity/title lookup for one arrangement.

    The feedback thread itself is authoritative for feedback state, but it does not carry the
    assignment title or participant names. Read those from the assignment record so the card can
    name a real person and a real task. A failed or malformed read must never block feedback, so
    every error degrades to an empty directory and the card falls back to role-only labels.
    """
    fetched = await CLIENT.call_tool("assignment_get", {"assignment_id": arrangement_id}, retryable=True)
    assignment = result_object(fetched) if fetched.get("ok") else None
    if assignment is None:
        return {}
    names_by_open_id: dict[str, str] = {}
    unique_names_by_role: dict[str, str] = {}
    for role, value in (("assigner", assignment.get("assigner")), ("recipient", assignment.get("recipients"))):
        participants = [
            participant
            for participant in (value if isinstance(value, list) else [value])
            if isinstance(participant, dict)
        ]
        resolved_names: list[str | None] = []
        for participant in participants:
            name = await _resolved_participant_name(participant)
            resolved_names.append(name)
            if name is None:
                continue
            for open_id in _participant_open_ids(participant):
                names_by_open_id.setdefault(open_id, name)
        if len(resolved_names) == 1 and (name := resolved_names[0]):
            unique_names_by_role[role] = name
    directory: dict[str, Any] = {}
    if title := _required_text(assignment.get("title")):
        directory["assignment_title"] = title
    if names_by_open_id:
        directory["names_by_open_id"] = names_by_open_id
    if unique_names_by_role:
        directory["unique_names_by_role"] = unique_names_by_role
    return directory


async def _resolved_participant_name(value: dict[str, Any]) -> str | None:
    for open_id in sorted(_participant_open_ids(value)):
        if name := await _resolve_feishu_display_name(open_id, _get_users_batch_impl):
            return name
    return None


def _participant_open_ids(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    open_ids: set[str] = set()
    for field in ("feishu_open_id", "open_id", "delivery_open_id"):
        if text := _required_text(value.get(field)):
            open_ids.add(text)
    for field in ("feishu_open_ids", "open_ids"):
        aliases = value.get(field)
        if isinstance(aliases, list):
            open_ids.update(text for item in aliases if (text := _required_text(item)))
    return open_ids


def _feedback_projection(payload: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {
        field: value for field in _PROJECTION_TEXT_FIELDS if (value := _required_text(payload.get(field))) is not None
    }
    attempts = _string_items(payload.get("attempts"))
    if attempts:
        projection["attempts"] = attempts
    options = _valid_options(payload)
    if options:
        projection["options"] = options
    return projection


def _shared_entry_lines(
    thread: dict[str, Any],
    *,
    directory: dict[str, Any] | None = None,
) -> list[str]:
    entries = thread.get("entries")
    if not isinstance(entries, list):
        return []
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("entry_type") == "private_note":
            continue
        content = _required_text(entry.get("raw_content"))
        if content is None:
            continue
        author = _entry_author_label(entry, directory=directory)
        version = entry.get("version")
        prefix = f"v{version} {author}" if isinstance(version, int) else author
        lines.append(f"{len(lines) + 1}. {prefix}: {content}")
    return lines


def _entry_author_label(
    entry: dict[str, Any],
    *,
    directory: dict[str, Any] | None,
) -> str:
    role = entry.get("author_role")
    role_label = _ROLE_LABELS.get(role, "参与者")
    name = _entry_author_name(entry, directory=directory, role=role)
    return f"{name} ({role_label})" if name is not None and name != role_label else role_label


def _entry_author_name(
    entry: dict[str, Any],
    *,
    directory: dict[str, Any] | None,
    role: Any,
) -> str | None:
    """Resolve a human author only from trusted Memory identity and assignment data."""
    if role not in _HUMAN_AUTHOR_ROLES:
        return None
    directory = _dict_value(directory)
    names_by_open_id = _dict_value(directory.get("names_by_open_id"))
    author_open_id = _required_text(entry.get("author_open_id"))
    if author_open_id is not None and (name := _required_text(names_by_open_id.get(author_open_id))):
        return name
    return _required_text(_dict_value(directory.get("unique_names_by_role")).get(role))


def _state_label(state: str) -> str:
    return {
        "open": "待处理",
        "blocked": "阻塞中",
        "updated_waiting_recipient_confirmation": "已更新, 待接收者确认",
        "ready_to_execute": "可继续执行",
        "resolved": "已解决",
    }.get(state, state)


def _plain_text_element(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "plain_text", "content": content}}


def _heading_element(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": f"**{content}**"}


def _labeled_text(label: str, value: Any) -> str | None:
    text = _required_text(value)
    return f"{label}: {text}" if text is not None else None


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _required_text(item)) is not None]


def _required_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _operator_open_id(session_id: str | None) -> str | None:
    if not isinstance(session_id, str) or not session_id.startswith(_SESSION_PREFIX):
        return None
    candidate = session_id[len(_SESSION_PREFIX) :]
    return candidate if _OPEN_ID_RE.fullmatch(candidate) else None
