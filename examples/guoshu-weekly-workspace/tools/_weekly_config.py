"""Configuration for the guoshu-weekly MCP client.

Mirrors the fusion-memory pattern: the process starter owns the endpoint and
credential, the agent never mints or inspects them.  Kept deliberately smaller
than fusion-memory's config because the demo has a single service identity
rather than a per-user token map -- see README for what a multi-user deployment
must add.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 2
MIN_MAX_RETRIES = 0
MAX_MAX_RETRIES = 5


class WeeklyConfigError(Exception):
    """A configuration failure whose message never contains a credential."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WeeklyMcpConfig:
    url: str
    token: str = field(repr=False)
    timeout_seconds: float
    max_retries: int


def _clamp_float(raw: str | None, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except TypeError, ValueError:
        return default
    return max(minimum, min(maximum, value))


def _clamp_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except TypeError, ValueError:
        return default
    return max(minimum, min(maximum, value))


def _is_loopback(hostname: str) -> bool:
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_mcp_url(raw: str) -> str:
    """Require an explicit /mcp endpoint, and HTTPS off loopback."""
    url = (raw or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise WeeklyConfigError("invalid_url", "GUOSHU_WEEKLY_MCP_URL must be http or https")
    if not parts.hostname:
        raise WeeklyConfigError("invalid_url", "GUOSHU_WEEKLY_MCP_URL has no host")
    if parts.path.rstrip("/") != "/mcp":
        raise WeeklyConfigError("invalid_url", "GUOSHU_WEEKLY_MCP_URL path must be exactly /mcp")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise WeeklyConfigError(
            "insecure_url",
            "GUOSHU_WEEKLY_MCP_URL must use HTTPS for non-loopback hosts",
        )
    return url


def build_config(env: Mapping[str, str] | None = None) -> WeeklyMcpConfig:
    source = env if env is not None else os.environ
    return WeeklyMcpConfig(
        url=validate_mcp_url(source.get("GUOSHU_WEEKLY_MCP_URL", "")),
        token=(source.get("GUOSHU_WEEKLY_MCP_TOKEN", "") or "").strip(),
        timeout_seconds=_clamp_float(
            source.get("GUOSHU_WEEKLY_MCP_TIMEOUT_SECONDS"),
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=MIN_TIMEOUT_SECONDS,
            maximum=MAX_TIMEOUT_SECONDS,
        ),
        max_retries=_clamp_int(
            source.get("GUOSHU_WEEKLY_MCP_MAX_RETRIES"),
            default=DEFAULT_MAX_RETRIES,
            minimum=MIN_MAX_RETRIES,
            maximum=MAX_MAX_RETRIES,
        ),
    )


CONFIG = build_config()
