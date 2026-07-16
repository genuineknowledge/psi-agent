from __future__ import annotations

from psi_agent.session.history_display import (
    ROLE_USER_FEEDBACK,
    ROLE_USER_SCHEDULE,
    SOURCE_SCHEDULE,
    feedback_content_for,
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


def test_messages_for_ai_maps_user_feedback() -> None:
    wire = [
        {"role": "assistant", "content": "answer"},
        {
            "role": ROLE_USER_FEEDBACK,
            "content": feedback_content_for("up"),
            "feedback": "up",
        },
    ]
    assert messages_for_ai(wire) == [
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": feedback_content_for("up")},
    ]


def test_displayable_skips_schedule_and_feedback_rows() -> None:
    assert is_displayable_chat_message({"role": "user", "content": "hi"})
    assert is_displayable_chat_message({"role": "assistant", "content": "yo"})
    assert not is_displayable_chat_message({"role": ROLE_USER_SCHEDULE, "content": "task"})
    assert not is_displayable_chat_message({"role": "assistant", "content": "HEARTBEAT_OK", "source": SOURCE_SCHEDULE})
    assert not is_displayable_chat_message(
        {"role": ROLE_USER_FEEDBACK, "content": feedback_content_for("down"), "feedback": "down"}
    )
    assert not is_displayable_chat_message({"role": "tool", "content": "x"})
    assert not is_displayable_chat_message({"role": "user", "content": "ping"})
    assert not is_displayable_chat_message({"role": "assistant", "content": "PONG"})
    assert is_displayable_chat_message({"role": "user", "content": "ping pong game"})


def test_tag_schedule_origin() -> None:
    assert tag_schedule_origin({"role": "assistant", "content": "ok"})["source"] == SOURCE_SCHEDULE
