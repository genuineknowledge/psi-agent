"""Shared iFLYTEK speech API configuration, authentication, and result helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

STT_ENDPOINT = "wss://iat-api.xfyun.cn/v2/iat"
TTS_ENDPOINT = "wss://tts-api.xfyun.cn/v2/tts"
STT_DOMAIN = "iat"
TTS_VOICE = "xiaoyan"

_PLACEHOLDER_HINTS = (
    "your_",
    "changeme",
    "change-me",
    "placeholder",
    "<",
    "xxx",
    "replace_me",
)


@dataclass(frozen=True)
class SpeechApiConfig:
    """Credentials for one fixed iFLYTEK speech WebSocket service."""

    app_id: str
    api_key: str
    api_secret: str
    endpoint: str
    service: str

    @property
    def ready(self) -> bool:
        return bool(self.app_id and self.api_key and self.api_secret)

    def not_ready_message(self) -> str:
        prefix = f"XFYUN_{self.service.upper()}_"
        return (
            f"iFLYTEK {self.service.upper()} is not configured. Set "
            f"{prefix}APP_ID, {prefix}API_KEY, and {prefix}API_SECRET "
            "(or the shared XFYUN_APP_ID, XFYUN_API_KEY, XFYUN_API_SECRET) "
            "in the Gateway process environment."
        )


@dataclass(frozen=True)
class SpeechResult:
    """JSON-serializable result returned by public workspace speech tools."""

    ok: bool
    message: str
    backend: str = "xfyun"
    text: str = ""
    path: str = ""
    sid: str = ""


class XfyunApiError(RuntimeError):
    """An error response returned by an iFLYTEK WebSocket API."""

    def __init__(self, code: int, message: str, sid: str = "") -> None:
        self.code = code
        self.sid = sid
        suffix = f" (sid={sid})" if sid else ""
        super().__init__(f"iFLYTEK API error {code}: {message}{suffix}")


def dumps_result(result: SpeechResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False)


def read_stt_config() -> SpeechApiConfig:
    """Read STT credentials from process environment; endpoint/domain stay fixed."""
    return _read_config("stt", STT_ENDPOINT)


def read_tts_config() -> SpeechApiConfig:
    """Read TTS credentials from process environment; endpoint/model stay fixed."""
    return _read_config("tts", TTS_ENDPOINT)


def _read_config(service: str, endpoint: str) -> SpeechApiConfig:
    prefix = f"XFYUN_{service.upper()}_"
    return SpeechApiConfig(
        app_id=_first_value(f"{prefix}APP_ID", "XFYUN_APP_ID"),
        api_key=_first_value(f"{prefix}API_KEY", "XFYUN_API_KEY"),
        api_secret=_first_value(f"{prefix}API_SECRET", "XFYUN_API_SECRET"),
        endpoint=endpoint,
        service=service,
    )


def _first_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not _is_placeholder(value):
            return value
    return ""


def _is_placeholder(value: str) -> bool:
    lower = value.lower()
    return any(hint in lower for hint in _PLACEHOLDER_HINTS)


def build_signed_url(config: SpeechApiConfig, *, now: datetime | None = None) -> str:
    """Create the official HMAC-SHA256 WebSocket authentication URL."""
    parsed = urlsplit(config.endpoint)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    date = format_datetime(current, usegmt=True)
    host = parsed.netloc
    request_line = f"GET {parsed.path} HTTP/1.1"
    signature_origin = f"host: {host}\ndate: {date}\n{request_line}"
    digest = hmac.new(
        config.api_secret.encode(),
        signature_origin.encode(),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode()
    authorization_origin = (
        f'api_key="{config.api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    query = urlencode(
        {
            "authorization": base64.b64encode(authorization_origin.encode()).decode(),
            "date": date,
            "host": host,
        }
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
