"""MiniMax multimodal API helpers (vision chat + image generation).

One pay-as-you-go API key (``sk-api-...``) works for both vision and image gen.
Only ``model`` and request path differ — not separate credentials per feature.

Official host (CN): ``https://api.minimaxi.com``
Auth: ``Authorization: Bearer <API_KEY>`` + ``Content-Type: application/json``
Legacy ``GroupId`` query param is **not** required for ``/v1/chat/completions`` or
``/v1/image_generation``.
"""

from __future__ import annotations

import base64
import re
from typing import Any

DEFAULT_API_HOST = "https://api.minimaxi.com"
PROVIDER_NAME = "minimax"
DEFAULT_VISION_MODEL = "MiniMax-M3"
DEFAULT_IMAGE_MODEL = "image-01"

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
IMAGE_GENERATION_PATH = "/v1/image_generation"

_ASPECT_RATIO_VALUES: tuple[tuple[str, float], ...] = (
    ("1:1", 1.0),
    ("16:9", 16 / 9),
    ("4:3", 4 / 3),
    ("3:2", 3 / 2),
    ("2:3", 2 / 3),
    ("3:4", 3 / 4),
    ("9:16", 9 / 16),
    ("21:9", 21 / 9),
)

_THINKING_PATTERNS = (re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),)


def normalize_api_host(host_or_base_url: str) -> str:
    """Accept ``https://api.minimaxi.com`` or ``.../v1``; return host root."""
    host = host_or_base_url.strip().rstrip("/")
    if host.endswith("/v1"):
        return host[:-3]
    return host


def chat_completions_url(host_or_base_url: str) -> str:
    return f"{normalize_api_host(host_or_base_url)}{CHAT_COMPLETIONS_PATH}"


def image_generation_url(host_or_base_url: str) -> str:
    return f"{normalize_api_host(host_or_base_url)}{IMAGE_GENERATION_PATH}"


def auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def nearest_aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "1:1"
    target = width / height
    return min(_ASPECT_RATIO_VALUES, key=lambda item: abs(item[1] - target))[0]


def snap_dimension(value: int, *, default: int = 512) -> int:
    if value <= 0:
        value = default
    value = max(512, min(2048, value))
    return (value // 8) * 8


def strip_thinking(content: str) -> str:
    text = content
    for pattern in _THINKING_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


def parse_base_resp(data: dict[str, Any]) -> tuple[int, str]:
    base = data.get("base_resp")
    if not isinstance(base, dict):
        return 0, ""
    code = base.get("status_code", 0)
    msg = base.get("status_msg", "")
    if not isinstance(code, int):
        return 0, ""
    return code, str(msg) if isinstance(msg, str) else ""


def extract_image_bytes(data: dict[str, Any]) -> tuple[bytes | None, str | None]:
    code, msg = parse_base_resp(data)
    if code != 0:
        detail = msg or "unknown error"
        return None, f"MiniMax image API status {code}: {detail}"

    inner = data.get("data")
    if not isinstance(inner, dict):
        return None, "MiniMax image API response missing data object"

    b64_list = inner.get("image_base64")
    if isinstance(b64_list, list) and b64_list:
        first = b64_list[0]
        if isinstance(first, str) and first.strip():
            try:
                return base64.b64decode(first), None
            except Exception as e:
                return None, f"MiniMax image base64 decode failed: {e!r}"

    url_list = inner.get("image_urls")
    if isinstance(url_list, list) and url_list:
        first_url = url_list[0]
        if isinstance(first_url, str) and first_url.strip():
            return None, f"MiniMax returned image URL (expected base64): {first_url[:120]}"

    return None, "MiniMax image API response missing image_base64"
