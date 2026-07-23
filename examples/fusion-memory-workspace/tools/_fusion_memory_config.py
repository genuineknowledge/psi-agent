from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import anyio

DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 2
MIN_MAX_RETRIES = 0
MAX_MAX_RETRIES = 5
_TOKEN_MAP_CACHE: dict[str, tuple[tuple[int, int, int], dict[str, object]]] = {}
_TOKEN_MAP_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class MemoryMcpConfig:
    url: str
    token: str = field(repr=False)
    token_map_file: str | None
    workspace_id: str
    session_id: str | None
    timeout_seconds: float
    max_retries: int


@dataclass(frozen=True)
class ResolvedMemoryConfig:
    url: str
    token: str = field(repr=False)
    workspace_id: str
    session_id: str | None
    timeout_seconds: float
    max_retries: int
    identity_key: str


class MemoryConfigError(Exception):
    """Safe configuration error whose message never contains credentials."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    token_map_file = (values.get("FUSION_MEMORY_TOKEN_MAP_FILE") or "").strip()
    return MemoryMcpConfig(
        url=validate_mcp_url(values.get("FUSION_MEMORY_MCP_URL") or ""),
        token=(values.get("FUSION_MEMORY_TOKEN") or "").strip(),
        token_map_file=os.path.abspath(os.path.expanduser(token_map_file)) if token_map_file else None,
        workspace_id=(values.get("FUSION_MEMORY_WORKSPACE_ID") or "fusion-memory").strip() or "fusion-memory",
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


async def resolve_memory_config(
    session_id: str,
    config: MemoryMcpConfig | None = None,
) -> ResolvedMemoryConfig:
    """Resolve credentials for a trusted psi-agent Session ID."""
    effective = CONFIG if config is None else config
    if not effective.url:
        raise MemoryConfigError("configuration_error", "FUSION_MEMORY_MCP_URL is not configured")

    trusted_session_id = session_id.strip()
    if effective.token_map_file is not None:
        open_id = _open_id_from_session(trusted_session_id)
        if open_id is None:
            raise MemoryConfigError(
                "memory_user_not_configured",
                "Fusion Memory is not configured for this Session",
            )
        token_map = await _read_token_map(effective.token_map_file)
        entry = token_map.get(open_id)
        if entry is None:
            raise MemoryConfigError(
                "memory_user_not_configured",
                "Fusion Memory is not configured for this user",
            )
        if not isinstance(entry, dict):
            raise MemoryConfigError("configuration_error", "Fusion Memory token-map entry is invalid")
        token = entry.get("token")
        workspace_id = entry.get("workspace_id")
        if not isinstance(token, str) or not token.strip():
            raise MemoryConfigError("configuration_error", "Fusion Memory token-map entry has no valid token")
        if workspace_id is not None and not isinstance(workspace_id, str):
            raise MemoryConfigError(
                "configuration_error",
                "Fusion Memory token-map entry has an invalid workspace_id",
            )
        return ResolvedMemoryConfig(
            url=effective.url,
            token=token.strip(),
            workspace_id=(workspace_id or "").strip() or effective.workspace_id,
            session_id=trusted_session_id,
            timeout_seconds=effective.timeout_seconds,
            max_retries=effective.max_retries,
            identity_key=f"feishu:{open_id}",
        )

    if not effective.token:
        raise MemoryConfigError("configuration_error", "FUSION_MEMORY_TOKEN is not configured")
    return ResolvedMemoryConfig(
        url=effective.url,
        token=effective.token,
        workspace_id=effective.workspace_id,
        session_id=effective.session_id or trusted_session_id or None,
        timeout_seconds=effective.timeout_seconds,
        max_retries=effective.max_retries,
        identity_key="legacy-single-user",
    )


def _open_id_from_session(session_id: str) -> str | None:
    prefix = "feishu-"
    if not session_id.startswith(prefix):
        return None
    open_id = session_id[len(prefix) :]
    return open_id if re.fullmatch(r"ou_[A-Za-z0-9_]+", open_id) else None


async def _read_token_map(path: str) -> dict[str, object]:
    token_map_path = anyio.Path(path)
    for attempt in range(2):
        before = await _token_map_signature(token_map_path)
        with _TOKEN_MAP_CACHE_LOCK:
            cached = _TOKEN_MAP_CACHE.get(path)
            if cached is not None and cached[0] == before:
                return cached[1]
        try:
            raw = await token_map_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemoryConfigError("configuration_error", "Fusion Memory token map is unavailable") from exc
        after = await _token_map_signature(token_map_path)
        if before != after:
            if attempt == 0:
                continue
            raise MemoryConfigError("configuration_error", "Fusion Memory token map changed while reading")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemoryConfigError("configuration_error", "Fusion Memory token map is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MemoryConfigError("configuration_error", "Fusion Memory token map must be a JSON object")
        _ensure_unique_tokens(payload)
        with _TOKEN_MAP_CACHE_LOCK:
            _TOKEN_MAP_CACHE[path] = (after, payload)
        return payload
    raise MemoryConfigError("configuration_error", "Fusion Memory token map is unavailable")


async def _token_map_signature(path: anyio.Path) -> tuple[int, int, int]:
    try:
        stat = await path.stat()
    except OSError as exc:
        raise MemoryConfigError("configuration_error", "Fusion Memory token map is unavailable") from exc
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def _ensure_unique_tokens(token_map: dict[str, object]) -> None:
    digests: set[str] = set()
    for entry in token_map.values():
        if not isinstance(entry, dict):
            continue
        token = entry.get("token")
        if not isinstance(token, str) or not token.strip():
            continue
        digest = hashlib.sha256(token.strip().encode()).hexdigest()
        if digest in digests:
            raise MemoryConfigError(
                "configuration_error",
                "Fusion Memory token map assigns one token to multiple users",
            )
        digests.add(digest)


CONFIG = build_memory_config()
FUSION_MEMORY_CONFIG = CONFIG
MemoryConfig = MemoryMcpConfig
