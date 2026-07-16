"""History message roles / provenance beyond the OpenAI Chat Completions set.

OpenAI-facing roles remain ``system`` / ``user`` / ``assistant`` / ``tool``.
Schedule-triggered turns are stored as ``user_schedule`` so Gateway display
history can whitelist real chat without string blacklists. Before calling the
AI backend, ``user_schedule`` is projected back to ``user``.

Thumbs-up / thumbs-down from the Web Console are stored as ``user_feedback``:
visible to the model (projected to ``user``) but never shown as chat bubbles.
"""

from __future__ import annotations

from typing import Any

# Stored in JSONL for schedule/heartbeat turns (not an upstream OpenAI role).
ROLE_USER_SCHEDULE = "user_schedule"

# Stored for Web Console like/dislike — model guidance, not a chat bubble.
ROLE_USER_FEEDBACK = "user_feedback"

# Stamped on assistant/tool rows produced during a schedule turn.
SOURCE_SCHEDULE = "schedule"

# Dropped when projecting to the AI wire format.
_DISPLAY_ONLY_KEYS = frozenset({"source", "feedback"})

# Whole-message SSE / transport keepalive tokens — never chat bubbles (exact match).
_SSE_KEEPALIVE_CONTENT = frozenset({"ping", "pong"})


def is_sse_keepalive_content(text: str) -> bool:
    """Whether ``text`` is a transport keepalive token (``ping`` / ``pong``)."""
    return text.strip().casefold() in _SSE_KEEPALIVE_CONTENT


_FEEDBACK_CONTENT = {
    "up": (
        "[User feedback] The previous assistant reply was helpful. "
        "Prefer similar quality, accuracy, and style in follow-up replies."
    ),
    "down": (
        "[User feedback] The previous assistant reply was not satisfactory. "
        "Adjust the approach; do not repeat the same mistakes."
    ),
}


def is_schedule_turn_message(msg: dict[str, Any]) -> bool:
    return msg.get("role") == ROLE_USER_SCHEDULE


def is_user_feedback_message(msg: dict[str, Any]) -> bool:
    return msg.get("role") == ROLE_USER_FEEDBACK


def feedback_content_for(kind: str) -> str:
    """Canonical model-facing text for a thumbs vote (``up`` / ``down``)."""
    return _FEEDBACK_CONTENT[kind]


def tag_schedule_origin(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy stamped for schedule-origin display filtering."""
    out = dict(msg)
    out["source"] = SOURCE_SCHEDULE
    return out


def messages_for_ai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project history rows into OpenAI-compatible messages for the AI backend.

    - ``user_schedule`` / ``user_feedback`` → ``user``
    - drop display-only keys (``source``, ``feedback``, …)
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        projected: dict[str, Any] = {k: v for k, v in msg.items() if k not in _DISPLAY_ONLY_KEYS}
        role = projected.get("role")
        if role in (ROLE_USER_SCHEDULE, ROLE_USER_FEEDBACK):
            projected["role"] = "user"
        out.append(projected)
    return out


def is_displayable_chat_message(msg: dict[str, Any]) -> bool:
    """Whether Gateway ``/history`` should expose this row as a chat bubble."""
    role = msg.get("role", "")
    if role in (ROLE_USER_SCHEDULE, ROLE_USER_FEEDBACK):
        return False
    if msg.get("source") == SOURCE_SCHEDULE:
        return False
    if role not in ("user", "assistant"):
        return False
    text = msg.get("content", "")
    if not isinstance(text, str) or not text:
        return False
    return not is_sse_keepalive_content(text)
