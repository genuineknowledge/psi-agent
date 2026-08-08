from __future__ import annotations

import json
import re
from typing import Any

import _feishu_impl
from _assignment_delivery import advance_delivery as _advance_delivery
from _assignment_delivery import sync_progress_card as _sync_progress_card
from _assignment_tool_common import CLIENT, dumps_result, invalid_argument
from _assignment_tool_common import result_object as _result_object
from feishu_task import _feishu_task_create_once

from psi_agent.session.runtime_context import get_session_id

_SESSION_PREFIX = "feishu-"
_OPEN_ID_RE = re.compile(r"ou_[A-Za-z0-9_]+")
_DUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?")
_RECEIVED_STATES = {"received", "plan_submitted", "in_progress"}


async def assignment_accept(assignment_id: str) -> str:
    """Accept a work assignment and publish one tracked Feishu task.

    The trusted Feishu Session identifies the operator. Only a listed recipient
    may accept. Receipt remains successful when Feishu task publication fails;
    Memory atomically grants one publication claim across all Gateways. Receipt
    remains successful when task publication needs manual reconciliation.
    """
    if not isinstance(assignment_id, str) or not assignment_id.strip():
        return invalid_argument("assignment_id must be a non-empty string")
    normalized_assignment_id = assignment_id.strip()
    operator_open_id = _operator_open_id(get_session_id())
    if operator_open_id is None:
        return _error(
            "assignment_operator_required",
            "Assignment acceptance requires a trusted Feishu user Session",
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
        return _error("assignment_invalid", "Fusion Memory returned an invalid assignment")
    title = _text(assignment.get("title")) or "工作安排"

    recipients = assignment.get("recipients")
    if not _participants_include_open_id(recipients, operator_open_id):
        return _error(
            "assignment_recipient_required",
            "Only a listed assignment recipient may accept this work",
        )

    state = assignment.get("state")
    if state not in {"assigned", *_RECEIVED_STATES}:
        return _error(
            "assignment_state_invalid",
            f"Assignment cannot be accepted from state {state!r}",
        )

    if state == "assigned":
        transition: dict[str, Any] = {"transition_type": "confirm_receipt"}
        revision = assignment.get("revision")
        if isinstance(revision, int):
            transition["expected_revision"] = revision
        accepted = await CLIENT.call_tool(
            "assignment_transition",
            {
                "assignment_id": normalized_assignment_id,
                "transition": transition,
            },
            retryable=False,
        )
        if not accepted.get("ok") and _is_invalid_request(accepted):
            accepted = await CLIENT.call_tool(
                "assignment_get",
                {"assignment_id": normalized_assignment_id},
                retryable=True,
            )
        if not accepted.get("ok"):
            return dumps_result(accepted)
        accepted_assignment = _result_object(accepted)
        if accepted_assignment is None:
            return _error("assignment_invalid", "Fusion Memory returned an invalid accepted assignment")
        if accepted_assignment.get("state") not in _RECEIVED_STATES:
            return _error(
                "assignment_state_invalid",
                "Assignment receipt was not confirmed",
            )
        assignment = accepted_assignment
        recipients = assignment.get("recipients")
        if not _participants_include_open_id(recipients, operator_open_id):
            return _error(
                "assignment_recipient_required",
                "Only a listed assignment recipient may accept this work",
            )

    recipient_open_ids = _recipient_task_open_ids(recipients, operator_open_id)
    accepted_delivery = await _advance_delivery(
        CLIENT,
        assignment_id=normalized_assignment_id,
        event="accepted",
        recipient_open_id=operator_open_id,
    )
    progress_card_update = await _project_progress_card(
        normalized_assignment_id,
        title,
        accepted_delivery,
    )

    claim = await CLIENT.call_tool(
        "assignment_publication",
        {
            "assignment_id": normalized_assignment_id,
            "action": "claim",
            "channel": "feishu_task",
        },
        retryable=False,
    )
    if not claim.get("ok"):
        error_code, error_message, retryable = _memory_error_fields(
            claim,
            fallback_code="assignment_publication_claim_failed",
            fallback_message="Could not claim Feishu task publication",
        )
        return _accepted_publication_error(
            normalized_assignment_id,
            error_code,
            error_message,
            retryable=retryable,
        )
    claim_result = _result_object(claim)
    if claim_result is None:
        return _accepted_publication_error(
            normalized_assignment_id,
            "assignment_publication_invalid",
            "Fusion Memory returned an invalid publication claim",
        )
    publication = claim_result.get("publication")
    if not isinstance(publication, dict):
        return _accepted_publication_error(
            normalized_assignment_id,
            "assignment_publication_invalid",
            "Fusion Memory returned an invalid publication record",
        )
    if claim_result.get("acquired") is not True:
        if publication.get("status") == "published":
            task_guid = _text(publication.get("task_guid"))
            if publication.get("channel") != "feishu_task" or task_guid is None:
                return _accepted_publication_error(
                    normalized_assignment_id,
                    "assignment_publication_invalid",
                    "Fusion Memory returned an invalid published record",
                )
            task_url = _text(publication.get("url")) or ""
            published_delivery = await _advance_delivery(
                CLIENT,
                assignment_id=normalized_assignment_id,
                event="task_published",
            )
            progress_card_update = await _project_progress_card(
                normalized_assignment_id,
                title,
                published_delivery,
            )
            result = {
                "ok": True,
                "assignment_id": normalized_assignment_id,
                "accepted": True,
                "published": True,
                "already_published": True,
                "task_guid": task_guid,
                "url": task_url,
                "discussion_invitation": _discussion_invitation(enabled=False, sent=False),
            }
            if progress_card_update is not None:
                result["progress_card_update"] = progress_card_update
            return dumps_result(result)
        return _accepted_publication_error(
            normalized_assignment_id,
            "assignment_publication_reconciliation_required",
            "Feishu task publication is already claimed or failed; reconcile it before another create",
        )
    claim_token = _text(claim_result.get("claim_token"))
    if claim_token is None or publication.get("status") != "claimed" or publication.get("channel") != "feishu_task":
        return _accepted_publication_error(
            normalized_assignment_id,
            "assignment_publication_invalid",
            "Fusion Memory returned an unusable publication claim",
        )

    task_result = _parse_tool_result(
        await _feishu_task_create_once(
            summary=title,
            description=_task_description(assignment, normalized_assignment_id),
            due=_task_due(assignment),
            assignees=",".join(recipient_open_ids),
            # Feishu task create currently rejects follower members with 1470500
            # in the bot-owned publish path; keep the assignees and embed the
            # assigner context in the description instead of passing followers.
            followers="",
            user_key=operator_open_id,
            identity="bot",
        )
    )
    if not task_result.get("ok") or not _text(task_result.get("task_guid")):
        finalized = await _finalize_publication(
            assignment_id=normalized_assignment_id,
            action="fail",
            claim_token=claim_token,
            publication={
                "error_code": _error_code(task_result),
                "error_message": _error_message(task_result),
                "published_by_open_id": operator_open_id,
            },
        )
        finalized_publication = _result_object(finalized)
        delivery_recorded = (
            finalized.get("ok") is True
            and finalized_publication is not None
            and finalized_publication.get("status") == "failed"
            and finalized_publication.get("channel") == "feishu_task"
        )
        return dumps_result(
            {
                "ok": False,
                "assignment_id": normalized_assignment_id,
                "accepted": True,
                "published": False,
                "delivery_recorded": delivery_recorded,
                "error": {
                    "code": "feishu_task_publish_failed",
                    "message": _error_message(task_result),
                    "retryable": False,
                },
            }
        )

    task_guid = _text(task_result.get("task_guid")) or ""
    task_url = _text(task_result.get("url")) or ""
    recorded = await _finalize_publication(
        assignment_id=normalized_assignment_id,
        action="complete",
        claim_token=claim_token,
        publication={
            "task_guid": task_guid,
            "url": task_url,
            "recipient_open_ids": recipient_open_ids,
            "published_by_open_id": operator_open_id,
        },
    )
    recorded_publication = _result_object(recorded)
    if (
        recorded.get("ok") is not True
        or recorded_publication is None
        or recorded_publication.get("status") != "published"
        or recorded_publication.get("channel") != "feishu_task"
        or _text(recorded_publication.get("task_guid")) != task_guid
    ):
        return dumps_result(
            {
                "ok": False,
                "assignment_id": normalized_assignment_id,
                "accepted": True,
                "published": True,
                "delivery_recorded": False,
                "task_guid": task_guid,
                "url": task_url,
                "error": {
                    "code": "assignment_delivery_record_failed",
                    "message": "Feishu task was created but its delivery record could not be saved",
                    "retryable": False,
                },
            }
        )
    invitation = await _send_discussion_invitation(operator_open_id)
    published_delivery = await _advance_delivery(
        CLIENT,
        assignment_id=normalized_assignment_id,
        event="task_published",
    )
    progress_card_update = await _project_progress_card(
        normalized_assignment_id,
        title,
        published_delivery,
    )
    result = {
        "ok": True,
        "assignment_id": normalized_assignment_id,
        "accepted": True,
        "published": True,
        "task_guid": task_guid,
        "url": task_url,
        "discussion_invitation": invitation,
    }
    if progress_card_update is not None:
        result["progress_card_update"] = progress_card_update
    return dumps_result(result)


async def _project_progress_card(
    assignment_id: str,
    title: str,
    advanced: dict[str, Any],
) -> dict[str, Any] | None:
    if _result_object(advanced) is None:
        return None
    updated = await _sync_progress_card(
        CLIENT,
        assignment_id=assignment_id,
        title=title,
    )
    if updated.get("ok") is True:
        return None
    return {
        "updated": False,
        "deferred": True,
        "error": updated.get("error")
        or {
            "code": "progress_card_update_failed",
            "message": "Progress card update was deferred",
            "retryable": True,
        },
    }


async def _finalize_publication(
    *,
    assignment_id: str,
    action: str,
    claim_token: str,
    publication: dict[str, Any],
) -> dict[str, Any]:
    return await CLIENT.call_tool(
        "assignment_publication",
        {
            "assignment_id": assignment_id,
            "action": action,
            "channel": "feishu_task",
            "claim_token": claim_token,
            "publication": publication,
        },
        retryable=False,
    )


def _operator_open_id(session_id: str) -> str | None:
    if not isinstance(session_id, str) or not session_id.startswith(_SESSION_PREFIX):
        return None
    candidate = session_id[len(_SESSION_PREFIX) :]
    return candidate if _OPEN_ID_RE.fullmatch(candidate) else None


def _is_invalid_request(result: dict[str, Any]) -> bool:
    error = result.get("error")
    return isinstance(error, dict) and error.get("code") == "invalid_request"


def _memory_error_fields(
    result: dict[str, Any],
    *,
    fallback_code: str,
    fallback_message: str,
) -> tuple[str, str, bool]:
    error = result.get("error")
    if isinstance(error, dict):
        code = _text(error.get("code")) or fallback_code
        message = _text(error.get("message")) or fallback_message
        retryable = error.get("retryable") is True
        return code, message, retryable
    return fallback_code, fallback_message, False


def _accepted_publication_error(
    assignment_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> str:
    return dumps_result(
        {
            "ok": False,
            "assignment_id": assignment_id,
            "accepted": True,
            "published": False,
            "error": {"code": code, "message": message, "retryable": retryable},
        }
    )


def _discussion_invitation(*, enabled: bool = True, sent: bool = False) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "message": "任务已发布。要不要和我一起讨论一版可评审的实施方案",
        "sent": sent,
    }


async def _send_discussion_invitation(operator_open_id: str) -> dict[str, Any]:
    invitation = _discussion_invitation()
    try:
        await _feishu_impl.send_message_impl(
            operator_open_id,
            _text(invitation.get("message")) or "",
            "open_id",
        )
    except Exception:
        return invitation
    return _discussion_invitation(enabled=False, sent=True)


def _participant_open_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    open_ids: list[str] = []
    for participant in value:
        if not isinstance(participant, dict):
            continue
        open_id = _text(participant.get("feishu_open_id"))
        if open_id and _OPEN_ID_RE.fullmatch(open_id) and open_id not in open_ids:
            open_ids.append(open_id)
    return open_ids


def _participant_aliases(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    aliases = _participant_open_ids([value])
    raw_aliases = value.get("feishu_open_ids")
    if not isinstance(raw_aliases, list):
        return aliases
    for raw_alias in raw_aliases:
        alias = _text(raw_alias)
        if alias and _OPEN_ID_RE.fullmatch(alias) and alias not in aliases:
            aliases.append(alias)
    return aliases


def _participants_include_open_id(value: Any, open_id: str) -> bool:
    if not isinstance(value, list):
        return False
    return any(open_id in _participant_aliases(participant) for participant in value)


def _recipient_task_open_ids(value: Any, operator_open_id: str) -> list[str]:
    if not isinstance(value, list):
        return []
    open_ids: list[str] = []
    for participant in value:
        aliases = _participant_aliases(participant)
        selected = operator_open_id if operator_open_id in aliases else (aliases[0] if aliases else None)
        if selected is not None and selected not in open_ids:
            open_ids.append(selected)
    return open_ids


def _task_due(assignment: dict[str, Any]) -> str:
    action_items = assignment.get("action_items")
    if not isinstance(action_items, list):
        return ""
    due_values: set[str] = set()
    for item in action_items:
        if not isinstance(item, dict):
            continue
        due = _first_object_text(item, ("deadline", "due"))
        if due is not None and _DUE_RE.fullmatch(due):
            due_values.add(due)
    return due_values.pop() if len(due_values) == 1 else ""


def _task_description(assignment: dict[str, Any], assignment_id: str) -> str:
    lines: list[str] = []
    original_request = _text(assignment.get("original_request"))
    if original_request is not None:
        lines.extend(["安排者原始内容 (原文或语音转写, 未改写)", original_request])
    analysis: list[str] = []
    sections = [
        ("背景", assignment.get("context")),
        ("期望结果", assignment.get("expected_outcome")),
    ]
    analysis.extend(f"{label}: {text}" for label, value in sections if (text := _text(value)))
    _append_task_items(analysis, "待确认缺口", assignment.get("gaps"))
    _append_task_items(analysis, "已识别风险", assignment.get("risks"))
    _append_task_items(analysis, "行动项", assignment.get("action_items"), include_metadata=True)
    if analysis:
        lines.extend(["Agent 分析整理 (非安排者原话)", *analysis])
    sources = _task_sources(assignment.get("evidence_refs"))
    if sources:
        lines.extend(["参考资料", *sources])
    lines.append(f"工作安排编号: {assignment_id}")
    return "\n\n".join(lines)


def _append_task_items(lines: list[str], label: str, value: Any, *, include_metadata: bool = False) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        text = _task_item_text(item, include_metadata=include_metadata)
        if text is not None:
            lines.append(f"{label}: {text}")


def _task_item_text(value: Any, *, include_metadata: bool) -> str | None:
    if not isinstance(value, dict):
        return None
    content = _first_object_text(value, ("description", "action", "title", "name"))
    if content is None or not include_metadata:
        return content
    details: list[str] = []
    owner = _participant_text(value.get("owner"))
    if owner is not None:
        details.append(f"负责人: {owner}")
    deadline = _first_object_text(value, ("deadline", "due", "due_at"))
    if deadline is not None:
        details.append(f"截止时间: {deadline}")
    status = _text(value.get("status"))
    if status is not None:
        details.append(f"状态: {status}")
    return " | ".join([content, *details])


def _participant_text(value: Any) -> str | None:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return None
    return _first_object_text(value, ("display_name", "name", "user_id", "feishu_open_id"))


def _first_object_text(value: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    for field in fields:
        text = _text(value.get(field))
        if text is not None:
            return text
    return None


def _task_sources(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    sources: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = _text(item.get("uri")) or _text(item.get("url"))
        if source is not None:
            sources.append(source)
    return sources


def _parse_tool_result(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "message": "Feishu task tool returned invalid JSON"}
    return result if isinstance(result, dict) else {"ok": False, "message": "Feishu task tool returned invalid data"}


def _error_code(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict) and error.get("code") is not None:
        return str(error["code"])
    return str(result.get("code") or "feishu_task_error")


def _error_message(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict) and _text(error.get("message")):
        return _text(error.get("message")) or "Feishu task publication failed"
    return _text(result.get("message")) or "Feishu task publication failed"


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _error(code: str, message: str) -> str:
    return dumps_result(
        {
            "ok": False,
            "error": {"code": code, "message": message, "retryable": False},
        }
    )
