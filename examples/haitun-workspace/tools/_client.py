from __future__ import annotations

import json
from typing import Any

import aiohttp

UNAVAILABLE_MESSAGE = "Fusion Memory request failed"


class MemoryToolError(RuntimeError):
    def __init__(self, *, error: str, cause: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.cause = cause
        self.message = message


async def post_json(base_url: str, path: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=_normalize_timeout_seconds(timeout_seconds))
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, json=payload) as response,
        ):
            try:
                data = await response.json()
            except aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError:
                data = {}
            if response.status >= 400:
                raise MemoryToolError(
                    error=_extract_error(data) or "request_failed",
                    cause=_extract_cause(data) or f"http_{response.status}",
                    message=_extract_message(data) or UNAVAILABLE_MESSAGE,
                )
            return data if isinstance(data, dict) else {}
    except MemoryToolError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise MemoryToolError(
            error="service_unavailable",
            cause="connection_failed",
            message="Fusion Memory service is not reachable. Run fusion-memory status or fusion-memory start.",
        ) from exc


def format_context_pack(pack: dict[str, Any], limit: int = 8) -> str:
    lines: list[str] = []
    for key in (
        "candidates",
        "current_views",
        "entity_profiles",
        "facts",
        "events",
        "source_spans",
    ):
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


def format_error_result(exc: Exception) -> str:
    if isinstance(exc, MemoryToolError):
        payload = {"ok": False, "error": exc.error, "cause": exc.cause, "message": exc.message}
    else:
        payload = {
            "ok": False,
            "error": "request_failed",
            "cause": "unexpected_tool_error",
            "message": "Fusion Memory request failed. Run fusion-memory doctor.",
        }
    return json.dumps(payload, ensure_ascii=False)


def _normalize_timeout_seconds(value: float) -> float:
    if value <= 0:
        return 30.0
    return max(0.1, min(120.0, value))


def _extract_message(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_error(data: Any) -> str | None:
    if isinstance(data, dict):
        value = data.get("error")
        return value if isinstance(value, str) and value else None
    return None


def _extract_cause(data: Any) -> str | None:
    if isinstance(data, dict):
        value = data.get("cause")
        return value if isinstance(value, str) and value else None
    return None
