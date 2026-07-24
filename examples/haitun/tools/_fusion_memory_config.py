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


@dataclass(frozen=True)
class MemoryMcpConfig:
    url: str
    token: str = field(repr=False)
    workspace_id: str
    session_id: str | None
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


def validate_mcp_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        _ = parts.port
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("FUSION_MEMORY_MCP_URL is invalid") from exc
    if not hostname or parts.path != "/mcp":
        raise ValueError("FUSION_MEMORY_MCP_URL must use exact path /mcp")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("FUSION_MEMORY_MCP_URL must not contain credentials, query, or fragment")
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and _is_loopback_host(hostname):
        return url
    raise ValueError("FUSION_MEMORY_MCP_URL must use HTTPS except for loopback development")


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def build_memory_config(env: Mapping[str, str] | None = None) -> MemoryMcpConfig:
    values = os.environ if env is None else env
    return MemoryMcpConfig(
        url=validate_mcp_url(values.get("FUSION_MEMORY_MCP_URL") or ""),
        token=(values.get("FUSION_MEMORY_TOKEN") or "").strip(),
        workspace_id=(values.get("FUSION_MEMORY_WORKSPACE_ID") or "haitun").strip() or "haitun",
        session_id=(values.get("FUSION_MEMORY_SESSION_ID") or "").strip() or None,
        timeout_seconds=_clamp_float(
            values.get("FUSION_MEMORY_MCP_TIMEOUT_SECONDS"),
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=MIN_TIMEOUT_SECONDS,
            maximum=MAX_TIMEOUT_SECONDS,
        ),
        max_retries=_clamp_int(
            values.get("FUSION_MEMORY_MCP_MAX_RETRIES"),
            default=DEFAULT_MAX_RETRIES,
            minimum=MIN_MAX_RETRIES,
            maximum=MAX_MAX_RETRIES,
        ),
    )


CONFIG = build_memory_config()
FUSION_MEMORY_CONFIG = CONFIG
MemoryConfig = MemoryMcpConfig
