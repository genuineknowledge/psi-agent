"""Map Feishu ``contact.user.created_v3`` → ``feishu.hr.user_created``."""

from __future__ import annotations

from typing import Any


def map_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    header_raw = raw.get("header")
    header: dict[str, Any] = header_raw if isinstance(header_raw, dict) else {}
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    if not isinstance(event, dict):
        return []

    obj = event.get("object")
    if not isinstance(obj, dict):
        # Some payloads nest under event.user
        obj = event.get("user") if isinstance(event.get("user"), dict) else event

    if not isinstance(obj, dict):
        return []

    open_id = str(obj.get("open_id") or "").strip()
    user_id = str(obj.get("user_id") or "").strip()
    name = str(obj.get("name") or "").strip()
    if not open_id and not user_id:
        return []

    subject = open_id or user_id
    event_id = str(header.get("event_id") or "").strip()
    payload: dict[str, Any] = {
        "open_id": open_id,
        "user_id": user_id,
    }
    if name:
        payload["name"] = name
    if event_id:
        payload["platform_event_id"] = event_id

    dept = obj.get("department_ids")
    if isinstance(dept, list):
        payload["department_ids"] = dept
    emp_type = obj.get("employee_type")
    if emp_type is not None:
        payload["employee_type"] = emp_type

    idem = f"feishu:user_created:{event_id}" if event_id else f"feishu:user_created:{subject}"
    return [
        {
            "schema_version": 1,
            "source": "feishu",
            "event": "feishu.hr.user_created",
            "payload": payload,
            "raw_event": "contact.user.created_v3",
            "raw_payload": {"open_id": open_id, "user_id": user_id},
            "idempotency_key": idem,
            "routing": {"open_id": open_id} if open_id else {},
        }
    ]
