from __future__ import annotations

from typing import Any

from _assignment_display import resolve_feishu_display_name as _resolve_feishu_display_name
from _assignment_tool_common import CLIENT, dumps_result, invalid_argument, parse_json_object
from _feishu_impl import get_users_batch_impl as _get_users_batch_impl

from psi_agent.session import runtime_context


async def assignment_upsert(assignment_json: str) -> str:
    """Create or idempotently refresh a Fusion Memory organization work assignment.

    ``assignment_json`` must encode an object with this shape:

    - ``title``: non-empty task title.
    - ``state``: normally ``"assigned"`` after the assigner confirms delivery.
    - ``assigner``: object containing stable ``user_id``, ``display_name``, and the
      current ``feishu_open_id`` when known.
    - ``recipients``: array of participant objects with the same identity fields.
    - ``original_request``: the assigner's exact message or voice transcript. Do not
      rewrite it or mix Agent analysis into it.
    - ``context`` and ``expected_outcome``: confirmed background and expected result.
    - ``evidence_refs``: source objects such as ``{"uri": "https://..."}``.
    - ``gaps``, ``risks``, and ``action_items``: arrays of structured objects.
    - ``idempotency_key``: stable key reused for the same logical assignment.

    Optional fields include ``observers``, ``plan``, ``delivery_records``, and
    ``closure_reason``. Participants are matched by stable ``user_id``; a changed
    ``feishu_open_id`` is delivery metadata, not a new person or a new assignment.
    """
    assignment, error = parse_json_object(assignment_json, "assignment_json")
    if error is not None or assignment is None:
        return invalid_argument(error or "assignment_json must be a JSON object")
    await _bind_assigner_to_current_feishu_session(assignment)
    _normalize_structured_arrays(assignment)
    result = await CLIENT.call_tool(
        "assignment_upsert",
        {"assignment": assignment},
        retryable=False,
    )
    return dumps_result(result)


async def _bind_assigner_to_current_feishu_session(assignment: dict[str, Any]) -> None:
    open_id = _open_id_from_session(runtime_context.get_session_id())
    if open_id is None:
        return
    display_name = await _resolve_feishu_display_name(open_id, _get_users_batch_impl)
    assigner = {
        "user_id": open_id,
        "feishu_open_id": open_id,
    }
    if display_name is not None:
        assigner["display_name"] = display_name
    assignment["assigner"] = assigner


def _open_id_from_session(session_id: str) -> str | None:
    prefix = "feishu-"
    if not isinstance(session_id, str) or not session_id.startswith(prefix):
        return None
    open_id = session_id[len(prefix) :].strip()
    return open_id if open_id.startswith("ou_") else None


def _normalize_structured_arrays(assignment: dict[str, Any]) -> None:
    for field in ("gaps", "risks", "action_items"):
        value = assignment.get(field)
        if isinstance(value, list):
            assignment[field] = [_object_or_description(item) for item in value]
    evidence_refs = assignment.get("evidence_refs")
    if isinstance(evidence_refs, list):
        assignment["evidence_refs"] = [_object_or_uri(item) for item in evidence_refs]


def _object_or_description(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"description": str(value)}


def _object_or_uri(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"uri": str(value)}
