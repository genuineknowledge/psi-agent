from __future__ import annotations

from _assignment_tool_common import CLIENT, dumps_result, invalid_argument


async def assignment_get(assignment_id: str) -> str:
    """Fetch a Fusion Memory organization work assignment by id."""
    if not isinstance(assignment_id, str) or not assignment_id.strip():
        return invalid_argument("assignment_id must be a non-empty string")
    result = await CLIENT.call_tool(
        "assignment_get",
        {"assignment_id": assignment_id.strip()},
        retryable=True,
    )
    return dumps_result(result)
