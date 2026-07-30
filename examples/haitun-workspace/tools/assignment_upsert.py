from __future__ import annotations

from _assignment_tool_common import CLIENT, dumps_result, invalid_argument, parse_json_object


async def assignment_upsert(assignment_json: str) -> str:
    """Create or idempotently retrieve a Fusion Memory organization work assignment."""
    assignment, error = parse_json_object(assignment_json, "assignment_json")
    if error is not None or assignment is None:
        return invalid_argument(error or "assignment_json must be a JSON object")
    result = await CLIENT.call_tool(
        "assignment_upsert",
        {"assignment": assignment},
        retryable=False,
    )
    return dumps_result(result)
