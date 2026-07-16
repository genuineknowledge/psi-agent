from __future__ import annotations

from psi_agent.session.conversation import Conversation
from psi_agent.session.history_display import ROLE_USER_FEEDBACK, feedback_content_for


def test_apply_user_feedback_replace_and_clear() -> None:
    conv = Conversation(messages=[{"role": "assistant", "content": "a"}])
    assert conv.apply_user_feedback("up") == "up"
    assert conv.messages[-1] == {
        "role": ROLE_USER_FEEDBACK,
        "content": feedback_content_for("up"),
        "feedback": "up",
    }
    assert conv.apply_user_feedback("down") == "down"
    assert sum(1 for m in conv.messages if m.get("role") == ROLE_USER_FEEDBACK) == 1
    assert conv.messages[-1]["feedback"] == "down"
    assert conv.apply_user_feedback("") == ""
    assert all(m.get("role") != ROLE_USER_FEEDBACK for m in conv.messages)
