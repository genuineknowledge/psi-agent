from __future__ import annotations

import json
from typing import Any

import aiohttp

UNAVAILABLE_MESSAGE = "Fusion Memory request failed"


async def post_json(base_url: str, path: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=_normalize_timeout_seconds(timeout_seconds))
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession(timeout=timeout) as session, session.post(url, json=payload) as response:
        try:
            data = await response.json()
        except aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError:
            data = {}
        if response.status >= 400:
            message = _extract_message(data)
            raise RuntimeError(message or UNAVAILABLE_MESSAGE)
        return data if isinstance(data, dict) else {}


def format_context_pack(pack: dict[str, Any], limit: int = 8) -> str:
    lines: list[str] = []
    for key in ("candidates", "current_views", "entity_profiles", "facts", "events", "source_spans"):
        items = pack.get(key)
        if not isinstance(items, list):
            continue
        for item in items[:limit]:
            if isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("summary")
                    or item.get("content")
                    or item.get("candidate", {}).get("text")
                    or str(item)
                )
            else:
                text = str(item)
            lines.append(f"- {text}")
    return "Fusion Memory context:\n" + "\n".join(lines) if lines else ""


def _normalize_timeout_seconds(value: float) -> float:
    if value <= 0:
        return 2.0
    return max(0.1, min(5.0, value))


def _extract_message(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return None
