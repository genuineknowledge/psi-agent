from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

_INTERNAL_IDENTIFIER_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])(?:ou|on|oc|cli|wa|om|feedback|thread|task)[_-][A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])user[-_][A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE),
)


def readable_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(pattern.search(text) for pattern in _INTERNAL_IDENTIFIER_PATTERNS):
        return None
    return text


async def resolve_feishu_display_name(
    open_id: str,
    fetch_users: Callable[..., Awaitable[dict[str, Any]]],
) -> str | None:
    names = await resolve_feishu_display_names({open_id}, fetch_users)
    return names.get(open_id)


async def resolve_feishu_display_names(
    open_ids: set[str],
    fetch_users: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, str]:
    normalized_ids = {open_id.strip() for open_id in open_ids if open_id.strip()}
    if not normalized_ids:
        return {}
    try:
        result = await fetch_users(",".join(sorted(normalized_ids)), user_id_type="open_id")
    except Exception:
        return {}
    if result.get("ok") is not True:
        return {}
    users = result.get("users")
    if not isinstance(users, list):
        return {}
    names: dict[str, str] = {}
    for user in users:
        if not isinstance(user, dict):
            continue
        open_id = user.get("open_id")
        if not isinstance(open_id, str) or open_id not in normalized_ids:
            continue
        if name := readable_name(user.get("name")):
            names[open_id] = name
    return names
