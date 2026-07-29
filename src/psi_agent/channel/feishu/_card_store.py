"""AppData-backed snapshots for single-use Feishu cards."""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio

from psi_agent._appdata import resolve_appdata_root

_SNAPSHOT_VERSION = 2
_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class CardSnapshot:
    """Card content and server-side callback routing metadata."""

    card: dict[str, Any]
    source: dict[str, Any] = field(default_factory=dict)
    business_context: dict[str, Any] = field(default_factory=dict)
    action_handlers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CardSnapshotClaim:
    """Result of atomically claiming a card callback."""

    status: Literal["claimed", "already_consumed", "not_found", "invalid"]
    snapshot: CardSnapshot | None = None


def _validate_message_id(message_id: str) -> None:
    if not _MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError(f"Invalid Feishu message_id: {message_id!r}")


async def _snapshot_path(message_id: str, appdata: str) -> anyio.Path:
    _validate_message_id(message_id)
    root = await resolve_appdata_root(appdata)
    return anyio.Path(root) / "feishu-card-snapshots" / f"{message_id}.json"


async def _write_consumed_marker(path: anyio.Path, status: str) -> None:
    await path.write_text(
        json.dumps({"version": _SNAPSHOT_VERSION, "status": status}) + "\n",
        encoding="utf-8",
    )
    await path.chmod(0o600)


async def save_card_snapshot(
    message_id: str,
    card: dict[str, Any],
    appdata: str = "",
    *,
    source: dict[str, Any] | None = None,
    business_context: dict[str, Any] | None = None,
    action_handlers: dict[str, str] | None = None,
) -> None:
    """Atomically persist the exact card sent to Feishu."""
    path = await _snapshot_path(message_id, appdata)
    directory = path.parent
    await directory.mkdir(parents=True, exist_ok=True)
    await directory.chmod(0o700)
    consumed = directory / f"{message_id}.consumed"
    if await consumed.exists():
        raise RuntimeError(f"Feishu card {message_id!r} was consumed before its snapshot was saved")

    temporary = directory / f".{message_id}.{uuid.uuid4().hex}.tmp"
    try:
        payload = {
            "version": _SNAPSHOT_VERSION,
            "card": card,
            "source": source or {},
            "business_context": business_context or {},
            "action_handlers": action_handlers or {},
        }
        await temporary.touch(mode=0o600, exist_ok=False)
        await temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        await temporary.chmod(0o600)
        if await consumed.exists():
            raise RuntimeError(f"Feishu card {message_id!r} was consumed before its snapshot was saved")
        await temporary.replace(path)
        await path.chmod(0o600)
        if await consumed.exists():
            await path.unlink()
            raise RuntimeError(f"Feishu card {message_id!r} was consumed before its snapshot was saved")
    finally:
        with contextlib.suppress(FileNotFoundError):
            await temporary.unlink()


async def pop_card_snapshot(message_id: str, appdata: str = "") -> CardSnapshotClaim:
    """Atomically claim a snapshot and retain a durable single-use tombstone."""
    path = await _snapshot_path(message_id, appdata)
    await path.parent.mkdir(parents=True, exist_ok=True)
    await path.parent.chmod(0o700)
    consumed = path.parent / f"{message_id}.consumed"
    if await consumed.exists():
        return CardSnapshotClaim(status="already_consumed")
    try:
        await path.rename(consumed)
    except FileNotFoundError:
        try:
            await consumed.touch(mode=0o600, exist_ok=False)
        except FileExistsError:
            return CardSnapshotClaim(status="already_consumed")
        await _write_consumed_marker(consumed, "not_found")
        return CardSnapshotClaim(status="not_found")

    try:
        payload = json.loads(await consumed.read_text(encoding="utf-8"))
    except json.JSONDecodeError, UnicodeDecodeError:
        await _write_consumed_marker(consumed, "invalid")
        return CardSnapshotClaim(status="invalid")
    if not isinstance(payload, dict) or payload.get("version") not in {1, _SNAPSHOT_VERSION}:
        await _write_consumed_marker(consumed, "invalid")
        return CardSnapshotClaim(status="invalid")
    card = payload.get("card")
    if not isinstance(card, dict):
        await _write_consumed_marker(consumed, "invalid")
        return CardSnapshotClaim(status="invalid")
    if payload.get("version") == 1:
        snapshot = CardSnapshot(card=card)
    else:
        source = payload.get("source")
        business_context = payload.get("business_context")
        action_handlers = payload.get("action_handlers")
        if (
            not isinstance(source, dict)
            or not isinstance(business_context, dict)
            or not isinstance(action_handlers, dict)
        ):
            await _write_consumed_marker(consumed, "invalid")
            return CardSnapshotClaim(status="invalid")
        if not all(
            isinstance(action_id, str) and isinstance(handler, str) for action_id, handler in action_handlers.items()
        ):
            await _write_consumed_marker(consumed, "invalid")
            return CardSnapshotClaim(status="invalid")
        snapshot = CardSnapshot(
            card=card,
            source=source,
            business_context=business_context,
            action_handlers=action_handlers,
        )
    await _write_consumed_marker(consumed, "consumed")
    return CardSnapshotClaim(status="claimed", snapshot=snapshot)
