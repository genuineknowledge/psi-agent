from __future__ import annotations

from psi_agent.session.history_display import (
    ROLE_USER_SCHEDULE,
    SOURCE_SCHEDULE,
    is_displayable_chat_message,
    messages_for_ai,
    tag_schedule_origin,
)


def test_messages_for_ai_maps_user_schedule_and_strips_source() -> None:
    wire = [
        {"role": "system", "content": "sys"},
        {"role": ROLE_USER_SCHEDULE, "content": "heartbeat task"},
        {"role": "assistant", "content": "HEARTBEAT_OK", "source": SOURCE_SCHEDULE},
        {"role": "user", "content": "hi"},
    ]
    assert messages_for_ai(wire) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "heartbeat task"},
        {"role": "assistant", "content": "HEARTBEAT_OK"},
        {"role": "user", "content": "hi"},
    ]


def test_displayable_skips_schedule_rows() -> None:
    assert is_displayable_chat_message({"role": "user", "content": "hi"})
    assert is_displayable_chat_message({"role": "assistant", "content": "yo"})
    assert not is_displayable_chat_message({"role": ROLE_USER_SCHEDULE, "content": "task"})
    assert not is_displayable_chat_message({"role": "assistant", "content": "HEARTBEAT_OK", "source": SOURCE_SCHEDULE})
    assert not is_displayable_chat_message({"role": "tool", "content": "x"})


def test_tag_schedule_origin() -> None:
    assert tag_schedule_origin({"role": "assistant", "content": "ok"})["source"] == SOURCE_SCHEDULE
