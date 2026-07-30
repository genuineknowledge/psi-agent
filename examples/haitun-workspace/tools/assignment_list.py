from __future__ import annotations

from _assignment_tool_common import CLIENT, bounded_limit, dumps_result, invalid_argument


async def assignment_list(
    participant_user_id: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> str:
    """List Fusion Memory organization work assignments."""
    normalized_participant = (
        participant_user_id.strip() if isinstance(participant_user_id, str) and participant_user_id.strip() else None
    )
    normalized_state = state.strip() if isinstance(state, str) and state.strip() else None
    if participant_user_id is not None and normalized_participant is None:
        return invalid_argument("participant_user_id must be a non-empty string")
    if state is not None and normalized_state is None:
        return invalid_argument("state must be a non-empty string")
    result = await CLIENT.call_tool(
        "assignment_list",
        {
            "participant_user_id": normalized_participant,
            "state": normalized_state,
            "limit": bounded_limit(limit),
        },
        retryable=True,
    )
    return dumps_result(result)
