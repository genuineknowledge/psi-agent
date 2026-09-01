from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from anyio.to_thread import run_sync as run_sync_in_worker_thread

from psi_agent._appdata import resolve_appdata_root

if os.name == "nt":
    import msvcrt
else:
    import fcntl

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
    auto_register_feishu: bool = False
    organization_id: str | None = None
    organization_chat_id: str | None = None
    feishu_app_id: str | None = field(default=None, repr=False)
    feishu_app_secret: str | None = field(default=None, repr=False)


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

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


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
    if parts.scheme == "http" and _is_trusted_plaintext_host(hostname):
        return url
    raise ValueError("FUSION_MEMORY_MCP_URL must use HTTPS except for loopback or private-network hosts")


def _is_trusted_plaintext_host(hostname: str) -> bool:
    """Hosts allowed to use plain http: loopback and RFC1918 private-network IPs.

    A same-LAN Fusion Memory deployment often terminates on a private IP without
    public TLS (e.g. ``http://192.168.x.x:PORT/mcp``). Loopback stays allowed for
    local development; public hosts still require HTTPS.
    """
    if hostname.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def build_memory_config(env: Mapping[str, str] | None = None) -> MemoryMcpConfig:
    values = os.environ if env is None else env
    token_map_file = (values.get("FUSION_MEMORY_TOKEN_MAP_FILE") or "").strip()
    return MemoryMcpConfig(
        url=validate_mcp_url(values.get("FUSION_MEMORY_MCP_URL") or ""),
        token=(values.get("FUSION_MEMORY_TOKEN") or "").strip(),
        token_map_file=os.path.abspath(os.path.expanduser(token_map_file)) if token_map_file else None,
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
        auto_register_feishu=_bool_env(values.get("FUSION_MEMORY_AUTO_REGISTER_FEISHU")),
        organization_id=(values.get("FUSION_MEMORY_ORGANIZATION_ID") or "").strip() or None,
        organization_chat_id=(values.get("FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID") or "").strip() or None,
        feishu_app_id=(values.get("PSI_FEISHU_APP_ID") or values.get("FUSION_MEMORY_FEISHU_APP_ID") or "").strip()
        or None,
        feishu_app_secret=(
            values.get("PSI_FEISHU_APP_SECRET") or values.get("FUSION_MEMORY_FEISHU_APP_SECRET") or ""
        ).strip()
        or None,
    )


async def resolve_memory_config(
    session_id: str,
    config: MemoryMcpConfig | None = None,
) -> ResolvedMemoryConfig:
    """Resolve credentials for a trusted psi-agent Session ID."""
    effective = await _prepare_memory_config(CONFIG if config is None else config)
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
            if not effective.auto_register_feishu:
                raise MemoryConfigError(
                    "memory_user_not_configured",
                    "Fusion Memory is not configured for this user",
                )
            entry = await _auto_register_feishu_user(effective, open_id)
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


async def refresh_feishu_registration(
    session_id: str,
    config: MemoryMcpConfig | None = None,
) -> ResolvedMemoryConfig:
    """Replace one Feishu user's rejected token with a newly registered token."""
    effective = await _prepare_memory_config(CONFIG if config is None else config)
    if not effective.auto_register_feishu:
        raise MemoryConfigError("registration_disabled", "Fusion Memory Feishu registration is disabled")
    open_id = _open_id_from_session(session_id.strip())
    if open_id is None:
        raise MemoryConfigError("memory_user_not_configured", "Fusion Memory registration requires a Feishu user")
    await _auto_register_feishu_user(effective, open_id, replace_existing=True)
    return await resolve_memory_config(session_id, effective)


async def _prepare_memory_config(config: MemoryMcpConfig) -> MemoryMcpConfig:
    if not config.auto_register_feishu:
        return config
    try:
        token_map_file = config.token_map_file
        if token_map_file is None:
            appdata_root = await resolve_appdata_root()
            token_map_file = os.path.join(appdata_root, "fusion-memory", "tokens.json")
            config = replace(config, token_map_file=token_map_file)
        await run_sync_in_worker_thread(_ensure_token_map_file_sync, token_map_file)
    except OSError as exc:
        raise MemoryConfigError("token_map_unavailable", "Fusion Memory token map is unavailable") from exc
    return config


async def _auto_register_feishu_user(
    config: MemoryMcpConfig,
    open_id: str,
    *,
    replace_existing: bool = False,
) -> dict[str, object]:
    if config.token_map_file is None:
        raise MemoryConfigError("configuration_error", "Fusion Memory token map is required for registration")
    if not config.organization_id or not config.feishu_app_id or not config.feishu_app_secret:
        raise MemoryConfigError(
            "registration_credentials_incomplete",
            "Fusion Memory Feishu registration credentials are incomplete",
        )
    try:
        response = await _register_feishu_user(
            url=config.url,
            app_id=config.feishu_app_id,
            app_secret=config.feishu_app_secret,
            organization_id=config.organization_id,
            feishu_open_id=open_id,
            display_name=None,
        )
    except httpx.HTTPStatusError as exc:
        transient = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
        code = "registration_unavailable" if transient else "registration_rejected"
        raise MemoryConfigError(
            code,
            "Fusion Memory Feishu registration request failed",
            retryable=transient,
        ) from exc
    except json.JSONDecodeError as exc:
        raise MemoryConfigError("registration_failed", "Fusion Memory Feishu registration response is invalid") from exc
    except (httpx.RequestError, OSError, TimeoutError) as exc:
        raise MemoryConfigError(
            "registration_unavailable",
            "Fusion Memory Feishu registration service is unavailable",
            retryable=True,
        ) from exc
    if response.get("ok") is not True:
        raise MemoryConfigError("registration_failed", "Fusion Memory Feishu registration failed")
    result = response.get("result")
    if not isinstance(result, dict):
        raise MemoryConfigError("registration_failed", "Fusion Memory Feishu registration returned no result")
    token = result.get("token")
    if not isinstance(token, str) or not token.strip():
        raise MemoryConfigError("registration_failed", "Fusion Memory Feishu registration returned no token")
    try:
        committed_entry = await _write_token_map_entry(
            config.token_map_file,
            open_id,
            token.strip(),
            config.workspace_id,
            replace_existing=replace_existing,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryConfigError("token_map_unavailable", "Fusion Memory token map is unavailable") from exc
    return committed_entry


async def _register_feishu_user(
    *,
    url: str,
    app_id: str,
    app_secret: str,
    organization_id: str,
    feishu_open_id: str,
    display_name: str | None,
) -> dict[str, object]:
    endpoint = _registration_url(url)
    assertion = _sign_registration_assertion(
        app_id=app_id,
        app_secret=app_secret,
        organization_id=organization_id,
        feishu_open_id=feishu_open_id,
        display_name=display_name,
    )
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
        response = await client.post(endpoint, json={"assertion": assertion})
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {"ok": False}


def _registration_url(mcp_url: str) -> str:
    return mcp_url.removesuffix("/mcp") + "/feishu/register"


def _sign_registration_assertion(
    *,
    app_id: str,
    app_secret: str,
    organization_id: str,
    feishu_open_id: str,
    display_name: str | None,
) -> str:
    payload = {
        "app_id": app_id,
        "organization_id": organization_id,
        "feishu_open_id": feishu_open_id,
        "display_name": display_name,
        "nonce": secrets.token_urlsafe(16),
        "issued_at": datetime.now(UTC).isoformat(),
    }
    signature = hmac.new(
        app_secret.encode("utf-8"), _canonical_json(payload).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    envelope = {"payload": payload, "signature": signature}
    return base64.urlsafe_b64encode(_canonical_json(envelope).encode("utf-8")).decode("ascii")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _write_token_map_entry(
    path: str,
    open_id: str,
    token: str,
    workspace_id: str,
    *,
    replace_existing: bool = False,
) -> dict[str, object]:
    return await run_sync_in_worker_thread(
        _write_token_map_entry_sync,
        path,
        open_id,
        token,
        workspace_id,
        replace_existing,
    )


async def _read_token_map(path: str) -> dict[str, object]:
    for attempt in range(2):
        before = await _token_map_signature(path)
        with _TOKEN_MAP_CACHE_LOCK:
            cached = _TOKEN_MAP_CACHE.get(path)
            if cached is not None and cached[0] == before:
                return cached[1]
        try:
            raw = _read_token_map_text(path)
        except OSError as exc:
            raise MemoryConfigError("configuration_error", "Fusion Memory token map is unavailable") from exc
        after = await _token_map_signature(path)
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


async def _token_map_signature(path: str) -> tuple[int, int, int]:
    try:
        total, digest_value = _token_map_signature_sync(path)
    except OSError as exc:
        raise MemoryConfigError("configuration_error", "Fusion Memory token map is unavailable") from exc
    return total, 0, digest_value


def _read_token_map_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _token_map_signature_sync(path: str) -> tuple[int, int]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    return total, int.from_bytes(digest.digest()[:8], "big")


def _ensure_token_map_file_sync(path: str) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with _token_map_file_lock(path):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)


@contextmanager
def _token_map_file_lock(path: str) -> Iterator[None]:
    descriptor = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(f"{path}.lock", 0o600)
        if os.name == "nt":
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"x")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _atomic_write_token_map_sync(path: str, payload: dict[str, object]) -> None:
    directory = os.path.dirname(path)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        if os.name != "nt":
            with suppress(OSError):
                directory_descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary_path)


def _write_token_map_entry_sync(
    path: str,
    open_id: str,
    token: str,
    workspace_id: str,
    replace_existing: bool = False,
) -> dict[str, object]:
    with _TOKEN_MAP_CACHE_LOCK, _token_map_file_lock(path):
        raw = _read_token_map_text(path)
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise MemoryConfigError("configuration_error", "Fusion Memory token map must be a JSON object")
        existing = payload.get(open_id)
        if existing is not None:
            if not isinstance(existing, dict):
                raise MemoryConfigError("configuration_error", "Fusion Memory token-map entry is invalid")
            if not replace_existing:
                return dict(existing)
        committed_entry: dict[str, object] = {"token": token, "workspace_id": workspace_id}
        payload[open_id] = committed_entry
        _atomic_write_token_map_sync(path, payload)
        _TOKEN_MAP_CACHE.pop(path, None)
        return committed_entry


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


def _bool_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


CONFIG = build_memory_config()
FUSION_MEMORY_CONFIG = CONFIG
MemoryConfig = MemoryMcpConfig
