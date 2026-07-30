from __future__ import annotations

from _assignment_tool_common import CLIENT, dumps_result, invalid_argument, parse_json_object


async def assignment_transition(assignment_id: str, transition_json: str) -> str:
    """Apply a state transition to a Fusion Memory organization work assignment."""
    if not isinstance(assignment_id, str) or not assignment_id.strip():
        return invalid_argument("assignment_id must be a non-empty string")
    transition, error = parse_json_object(transition_json, "transition_json")
    if error is not None or transition is None:
        return invalid_argument(error or "transition_json must be a JSON object")
    result = await CLIENT.call_tool(
        "assignment_transition",
        {"assignment_id": assignment_id.strip(), "transition": transition},
        retryable=False,
    )
    return dumps_result(result)
