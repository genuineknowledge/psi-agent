"""Map Feishu ``im.chat.member.user.added_v1`` → Session envelope(s).

``map_event(raw)`` must return a list of dicts suitable for ``POST /events``.
One platform push may expand to multiple envelopes (one per new member).
"""

from __future__ import annotations

from typing import Any


def map_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    if not isinstance(event, dict):
        return []

    header_value = raw.get("header")
    header: dict[str, Any] = header_value if isinstance(header_value, dict) else {}
    event_id = str(header.get("event_id") or raw.get("uuid") or "").strip()
    occurred_at = str(header.get("create_time") or raw.get("ts") or "").strip()
    chat_id = str(event.get("chat_id") or "").strip()
    if not chat_id:
        return []

    operator = event.get("operator_id") or event.get("operator") or {}
    operator_open_id = ""
    if isinstance(operator, dict):
        operator_open_id = str(operator.get("open_id") or operator.get("user_id") or "").strip()

    users = event.get("users") or event.get("user_list") or []
    if not isinstance(users, list) or not users:
        # Some payloads put a single user object.
        single = event.get("user") or event.get("users")
        users = [single] if isinstance(single, dict) else []

    out: list[dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        uid = user.get("user_id") if isinstance(user.get("user_id"), dict) else user
        if not isinstance(uid, dict):
            uid = user
        member_open_id = str(uid.get("open_id") or uid.get("user_id") or "").strip()
        if not member_open_id:
            continue
        member_name = str(user.get("name") or "").strip()
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "member_open_id": member_open_id,
        }
        if member_name:
            payload["member_name"] = member_name
        if operator_open_id:
            payload["operator_open_id"] = operator_open_id

        routing: dict[str, Any] = {}
        # Prefer operator (who invited) for per-user Session routing when Gateway is on.
        if operator_open_id:
            routing["open_id"] = operator_open_id

        envelope: dict[str, Any] = {
            "schema_version": 1,
            "source": "feishu",
            "event": "feishu.chat.member_added",
            "payload": payload,
            "raw_event": "im.chat.member.user.added_v1",
            "raw_payload": {"chat_id": chat_id, "member_open_id": member_open_id},
            "routing": routing,
        }
        # Deduplicate platform redelivery by the occurrence ID. A chat/member
        # pair alone is not unique because the same person can leave and rejoin.
        if event_id:
            envelope["idempotency_key"] = f"feishu:member_added:{event_id}:{member_open_id}"
        if occurred_at:
            envelope["occurred_at"] = occurred_at
        out.append(envelope)
    return out
