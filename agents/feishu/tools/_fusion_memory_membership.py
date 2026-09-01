from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from _feishu_impl import list_chat_members_impl

MEMBERSHIP_TTL_SECONDS = 300
MEMBERSHIP_REFRESH_SECONDS = 240
_SYNC_CACHE: dict[str, float] = {}
_SYNC_CACHE_LOCK = threading.RLock()


class MembershipSyncError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


async def build_current_assertion(config: Any, open_id: str) -> str:
    organization_id = _required_config(config, "organization_id")
    chat_id = _required_config(config, "organization_chat_id")
    app_id = _required_config(config, "feishu_app_id")
    app_secret = _required_config(config, "feishu_app_secret")
    member_open_id = _required_text(open_id, "open_id")

    roster = await list_chat_members_impl(chat_id)
    if roster.get("ok") is not True:
        raise MembershipSyncError(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            retryable=True,
        )
    members = roster.get("members")
    if not isinstance(members, list):
        raise MembershipSyncError(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            retryable=True,
        )
    active = any(isinstance(member, dict) and member.get("id") == member_open_id for member in members)
    verified_at = datetime.now(UTC)
    payload = {
        "app_id": app_id,
        "organization_id": organization_id,
        "source_group_id": chat_id,
        "feishu_open_id": member_open_id,
        "membership_status": "active" if active else "disabled",
        "nonce": secrets.token_urlsafe(16),
        "verified_at": verified_at.isoformat(),
        "expires_at": (verified_at + timedelta(seconds=MEMBERSHIP_TTL_SECONDS)).isoformat(),
    }
    signature = hmac.new(
        app_secret.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return base64.urlsafe_b64encode(
        _canonical_json({"payload": payload, "signature": signature}).encode("utf-8")
    ).decode("ascii")


async def sync_current_membership(config: Any, open_id: str, *, force: bool = False) -> dict[str, Any]:
    member_open_id = _required_text(open_id, "open_id")
    cache_key = _cache_key(config, member_open_id)
    now = time.monotonic()
    with _SYNC_CACHE_LOCK:
        if not force and _SYNC_CACHE.get(cache_key, 0.0) > now:
            return {"ok": True, "cached": True}
    try:
        assertion = await build_current_assertion(config, member_open_id)
        payload = _assertion_payload(assertion)
        result = await _post_membership_assertion(config, assertion)
    except MembershipSyncError as exc:
        return _error(exc.code, str(exc), exc.retryable)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429 or exc.response.status_code >= 500:
            return _error(
                "organization_membership_unverified",
                "Organization membership could not be verified",
                True,
            )
        return _error(
            "organization_membership_rejected",
            "Organization membership proof was rejected",
            False,
        )
    except httpx.RequestError, OSError, TimeoutError:
        return _error(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            True,
        )
    except Exception:
        return _error(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            True,
        )
    if result.get("ok") is not True:
        return _error(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            True,
        )
    if payload.get("membership_status") != "active":
        with _SYNC_CACHE_LOCK:
            _SYNC_CACHE.pop(cache_key, None)
        return _error("organization_access_denied", "Organization access is not available", False)
    with _SYNC_CACHE_LOCK:
        _SYNC_CACHE[cache_key] = now + MEMBERSHIP_REFRESH_SECONDS
    return {"ok": True, "cached": False}


async def _post_membership_assertion(config: Any, assertion: str) -> dict[str, Any]:
    mcp_url = _required_config(config, "url")
    timeout = min(30.0, max(0.1, float(getattr(config, "timeout_seconds", 30.0))))
    endpoint = mcp_url.removesuffix("/mcp") + "/feishu/membership"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, json={"assertion": assertion})
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {"ok": False}


def _assertion_payload(assertion: str) -> dict[str, Any]:
    try:
        envelope = json.loads(base64.urlsafe_b64decode(assertion.encode("ascii")))
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MembershipSyncError(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise MembershipSyncError(
            "organization_membership_unverified",
            "Organization membership could not be verified",
            retryable=True,
        )
    return payload


def _cache_key(config: Any, open_id: str) -> str:
    raw = "\0".join(
        (
            str(getattr(config, "organization_id", "")),
            str(getattr(config, "organization_chat_id", "")),
            open_id,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _required_config(config: Any, name: str) -> str:
    try:
        return _required_text(getattr(config, name, None), name)
    except ValueError as exc:
        raise MembershipSyncError(
            "organization_membership_unverified",
            "Organization membership is not configured",
            retryable=False,
        ) from exc


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}
