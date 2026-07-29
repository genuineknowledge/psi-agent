"""Session-layer event envelope: thin accept shape for ``POST /events``.

Session does **not** own the business event registry. Channel (driven by
agent-package ``channel_events/``) defines which platform / synthetic
events exist and forwards envelopes here. Session only checks envelope
shape, then ``TriggerRegistry`` matches hooks.

See ``session/AGENTS.md`` § Event / Trigger and
``examples/haitun-workspace/channel_events/README.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

SCHEMA_VERSION = 1

SOURCE_FEISHU = "feishu"
SOURCE_TELEGRAM = "telegram"
SOURCE_GATEWAY = "gateway"
SOURCE_TEST = "test"

# Soft allow-list for ``source`` (not the business event catalog).
KNOWN_SOURCES = frozenset({SOURCE_FEISHU, SOURCE_TELEGRAM, SOURCE_GATEWAY, SOURCE_TEST})

# Convenience aliases for tests / docs (not an admission gate).
EVENT_FEISHU_CHAT_MEMBER_ADDED = "feishu.chat.member_added"
EVENT_FEISHU_CHAT_MEMBER_REMOVED = "feishu.chat.member_removed"
EVENT_FEISHU_IM_MESSAGE_RECEIVED = "feishu.im.message_received"
EVENT_TELEGRAM_CHAT_MEMBER_JOINED = "telegram.chat.member_joined"


@dataclass(slots=True)
class EventEnvelope:
    """One delivered event occurrence (one ``POST /events`` body)."""

    schema_version: int
    source: str
    event: str
    payload: dict[str, Any]
    occurred_at: str = ""
    idempotency_key: str = ""
    routing: dict[str, Any] = field(default_factory=dict)
    # Optional platform-native type (e.g. Feishu ``im.chat.member.user.added_v1``).
    raw_event: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


class EventProtocolError(ValueError):
    """Invalid envelope — Session maps this to HTTP 400."""


def parse_event_envelope(raw: object) -> EventEnvelope:
    """Parse a JSON-decoded body into ``EventEnvelope``.

    **Thin gate only**: schema version, non-empty ``source``/``event``,
    ``payload`` object. Does **not** require a Session-side catalog of
    business event names — those live under agent ``channel_events/``.
    """
    if not isinstance(raw, dict):
        raise EventProtocolError("event body must be a JSON object")

    version = raw.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise EventProtocolError("schema_version must be an int")
    if version != SCHEMA_VERSION:
        raise EventProtocolError(f"unsupported schema_version {version}; expected {SCHEMA_VERSION}")

    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise EventProtocolError("source must be a non-empty string")
    source = source.strip().casefold()
    if source not in KNOWN_SOURCES:
        raise EventProtocolError(f"unknown source {source!r}; known={sorted(KNOWN_SOURCES)}")

    event = raw.get("event")
    if not isinstance(event, str) or not event.strip():
        raise EventProtocolError("event must be a non-empty string")
    event = event.strip()

    payload_raw = raw.get("payload")
    if not isinstance(payload_raw, dict):
        raise EventProtocolError("payload must be a JSON object")
    payload = cast(dict[str, Any], payload_raw).copy()

    occurred_at = raw.get("occurred_at", "")
    if occurred_at is None:
        occurred_at = ""
    if not isinstance(occurred_at, str):
        raise EventProtocolError("occurred_at must be a string when present")

    idem = raw.get("idempotency_key", "")
    if idem is None:
        idem = ""
    if not isinstance(idem, str):
        raise EventProtocolError("idempotency_key must be a string when present")

    routing_raw = raw.get("routing", {})
    if routing_raw is None:
        routing_raw = {}
    if not isinstance(routing_raw, dict):
        raise EventProtocolError("routing must be a JSON object when present")

    raw_event = raw.get("raw_event", "")
    if raw_event is None:
        raw_event = ""
    if not isinstance(raw_event, str):
        raise EventProtocolError("raw_event must be a string when present")

    raw_payload_raw = raw.get("raw_payload", {})
    if raw_payload_raw is None:
        raw_payload_raw = {}
    if not isinstance(raw_payload_raw, dict):
        raise EventProtocolError("raw_payload must be a JSON object when present")

    return EventEnvelope(
        schema_version=version,
        source=source,
        event=event,
        payload=payload,
        occurred_at=occurred_at.strip(),
        idempotency_key=idem.strip(),
        routing=cast(dict[str, Any], routing_raw).copy(),
        raw_event=raw_event.strip(),
        raw_payload=cast(dict[str, Any], raw_payload_raw).copy(),
    )


def filter_matches(payload: dict[str, Any], filt: dict[str, Any]) -> bool:
    """Exact subset match: every filter key must equal ``payload[key]``."""
    return all(payload.get(key) == expected for key, expected in filt.items())
