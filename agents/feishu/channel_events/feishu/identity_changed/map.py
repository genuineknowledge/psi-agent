"""Map Feishu ``contact.user.updated_v3`` → ``feishu.hr.identity_changed``.

Only emits when identity-relevant fields changed. Feishu puts previous values
of changed fields in ``old_object`` (keys present there ≈ what changed).
"""

from __future__ import annotations

from typing import Any

# Fields that count as「身份转变」for HR triggers (not avatar/phone/email noise).
_IDENTITY_TOP_KEYS = frozenset(
    {
        "department_ids",
        "job_title",
        "employee_type",
        "employee_no",
        "job_level_id",
        "job_family_id",
        "leader_user_id",
        "dotted_line_leader_user_ids",
        "status",
        "orders",  # department order / primary dept shifts often land here
    }
)

_STATUS_IDENTITY_KEYS = frozenset(
    {
        "is_frozen",
        "is_resigned",
        "is_activated",
        "is_exited",
        "is_unjoin",
    }
)


def map_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    header_raw = raw.get("header")
    header: dict[str, Any] = header_raw if isinstance(header_raw, dict) else {}
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    if not isinstance(event, dict):
        return []

    obj = event.get("object")
    old = event.get("old_object")
    if not isinstance(obj, dict):
        return []
    if not isinstance(old, dict):
        old = {}

    changed = _identity_changes(obj, old)
    if not changed:
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
        "changed_fields": sorted(changed),
        "before": {k: old[k] for k in changed if k in old},
        "after": {k: obj[k] for k in changed if k in obj},
    }
    if name:
        payload["name"] = name
    if event_id:
        payload["platform_event_id"] = event_id

    # Prefer new values; department_ids often only reliable on old_object per Feishu notes.
    dept_after = obj.get("department_ids")
    dept_before = old.get("department_ids")
    if isinstance(dept_after, list):
        payload["department_ids"] = dept_after
    elif isinstance(dept_before, list):
        payload["department_ids"] = dept_before

    if "job_title" in obj:
        payload["job_title"] = obj.get("job_title")
    elif "job_title" in old:
        payload["job_title_before"] = old.get("job_title")
    if "employee_type" in obj:
        payload["employee_type"] = obj.get("employee_type")
    elif "employee_type" in old:
        payload["employee_type_before"] = old.get("employee_type")

    idem = (
        f"feishu:identity_changed:{event_id}"
        if event_id
        else f"feishu:identity_changed:{subject}:{'-'.join(sorted(changed))}"
    )
    return [
        {
            "schema_version": 1,
            "source": "feishu",
            "event": "feishu.hr.identity_changed",
            "payload": payload,
            "raw_event": "contact.user.updated_v3",
            "raw_payload": {
                "open_id": open_id,
                "changed_fields": sorted(changed),
            },
            "idempotency_key": idem,
            "routing": {"open_id": open_id} if open_id else {},
        }
    ]


def _identity_changes(obj: dict[str, Any], old: dict[str, Any]) -> set[str]:
    """Return identity field names that Feishu reported as changed."""
    changed: set[str] = set()
    # Primary signal: keys in old_object (Feishu: only changed fields' old values).
    for key in old:
        if key not in _IDENTITY_TOP_KEYS:
            continue
        if key == "status":
            changed.add("status")
            changed.update(_status_sub_changes(obj.get("status"), old.get("status")))
        else:
            changed.add(key)
    return changed


def _status_sub_changes(new_status: Any, old_status: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(old_status, dict):
        return out
    new_d = new_status if isinstance(new_status, dict) else {}
    for key in _STATUS_IDENTITY_KEYS:
        if key not in old_status:
            continue
        if key not in new_d or new_d.get(key) != old_status.get(key):
            out.add(f"status.{key}")
    return out
