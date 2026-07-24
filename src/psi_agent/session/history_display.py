"""Chat-turn provenance via ``kind`` (OpenAI ``role`` stays wire-compatible).

Finalized protocol (2026-07-17):

- ``kind: "chat"`` — ordinary Channel / Web Console turns (display)
- ``kind: "schedule.silent"`` — schedule trigger input, or silent schedule result
  (never display; schedule *user* rows are always this)
- ``kind: "schedule.display"`` — schedule *assistant* result that should surface
- ``kind: "compacted"`` — compaction summary (system-side; not a chat bubble)

Legacy aliases still accepted when reading JSONL:

- ``chat_type: "common"`` → ``chat``
- ``chat_type: "schedule"`` → ``schedule.silent``
- roles ``user_schedule`` / ``assistant_schedule`` → schedule.silent

AI requests strip display-only keys and rewrite legacy roles via ``messages_for_ai``.
"""

from __future__ import annotations

import re
from typing import Any

KIND_CHAT = "chat"
KIND_SCHEDULE_SILENT = "schedule.silent"
KIND_SCHEDULE_DISPLAY = "schedule.display"
KIND_COMPACTED = "compacted"

KIND_KEY = "kind"

# Legacy field from the preliminary design (session层设计.txt).
CHAT_TYPE_KEY = "chat_type"
CHAT_TYPE_COMMON = "common"
CHAT_TYPE_SCHEDULE = "schedule"

_DISPLAY_ONLY_KEYS = frozenset({KIND_KEY, CHAT_TYPE_KEY})

_WIRE_ROLES = frozenset({"system", "user", "assistant", "tool"})

_LEGACY_ROLE_TO_WIRE: dict[str, str] = {
    "user_schedule": "user",
    "assistant_schedule": "assistant",
}

_KNOWN_KINDS = frozenset({KIND_CHAT, KIND_SCHEDULE_SILENT, KIND_SCHEDULE_DISPLAY, KIND_COMPACTED})

# Presentation-only strip of wire transfer markers (Gateway history projection).
_TRANSFER_MARKER_RE = re.compile(r"\[(?:SEND|RECV):[^\]]*\]")
_SEND_PATH_RE = re.compile(r"\[SEND:([^\]]*)\]")


def normalize_kind(raw: object) -> str:
    """Return a known ``kind``; unknown / empty → ``chat``."""
    if not isinstance(raw, str):
        return KIND_CHAT
    value = raw.strip().casefold()
    if value in _KNOWN_KINDS:
        return value
    if value == CHAT_TYPE_COMMON:
        return KIND_CHAT
    if value == CHAT_TYPE_SCHEDULE:
        return KIND_SCHEDULE_SILENT
    return KIND_CHAT


def wire_role(role: object) -> str | None:
    """Map a stored role to an OpenAI wire role, or ``None`` if unusable."""
    if not isinstance(role, str):
        return None
    if role in _WIRE_ROLES:
        return role
    mapped = _LEGACY_ROLE_TO_WIRE.get(role)
    if mapped is not None:
        return mapped
    if role.startswith("user_"):
        return "user"
    if role.startswith("assistant_"):
        return "assistant"
    return None


def message_kind(msg: dict[str, Any]) -> str:
    """Resolve provenance kind for a stored message."""
    role = msg.get("role")
    if isinstance(role, str) and (role in _LEGACY_ROLE_TO_WIRE or role.endswith("_schedule")):
        return KIND_SCHEDULE_SILENT
    if KIND_KEY in msg:
        return normalize_kind(msg.get(KIND_KEY))
    if CHAT_TYPE_KEY in msg:
        return normalize_kind(msg.get(CHAT_TYPE_KEY))
    return KIND_CHAT


def is_schedule_chat(msg: dict[str, Any]) -> bool:
    kind = message_kind(msg)
    return kind in {KIND_SCHEDULE_SILENT, KIND_SCHEDULE_DISPLAY}


def with_kind(msg: dict[str, Any], kind: str) -> dict[str, Any]:
    """Shallow copy with ``kind`` set (and legacy ``chat_type`` dropped)."""
    out = {k: v for k, v in msg.items() if k != CHAT_TYPE_KEY}
    out[KIND_KEY] = normalize_kind(kind)
    return out


def with_chat_type(msg: dict[str, Any], chat_type: str) -> dict[str, Any]:
    """Backward-compatible helper: map old ``chat_type`` names onto ``kind``."""
    return with_kind(msg, chat_type)


def messages_for_ai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project history for the AI backend: drop display-only keys; fix legacy roles."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = wire_role(msg.get("role"))
        if role is None:
            continue
        projected = {k: v for k, v in msg.items() if k not in _DISPLAY_ONLY_KEYS}
        projected["role"] = role
        out.append(projected)
    return out


def strip_transfer_markers(text: str) -> str:
    """Remove ``[SEND:…]`` / ``[RECV:…]`` from display text (Gateway projection)."""
    cleaned = _TRANSFER_MARKER_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_send_paths(text: str) -> list[str]:
    """Return ``[SEND:…]`` paths in order (stripped); empty / whitespace skipped."""
    if not isinstance(text, str) or not text:
        return []
    out: list[str] = []
    for match in _SEND_PATH_RE.finditer(text):
        path = match.group(1).strip()
        if path:
            out.append(path)
    return out


def is_displayable_chat_message(msg: dict[str, Any]) -> bool:
    """Whether Gateway ``/history`` should expose this row as a chat bubble.

    Whitelist by provenance ``kind`` (not content blacklist):

    - ``chat`` user/assistant with non-empty content → yes
    - ``schedule.display`` assistant with non-empty content → yes
    - ``schedule.silent`` / ``compacted`` / tools / system → no
    """
    kind = message_kind(msg)
    role = wire_role(msg.get("role"))
    if role not in ("user", "assistant"):
        return False
    text = msg.get("content", "")
    if not isinstance(text, str) or not text.strip():
        return False

    if kind == KIND_CHAT:
        # Legacy untagged heartbeat assistant replies (pre-kind JSONL).
        return text.strip() != "HEARTBEAT_OK"
    if kind == KIND_SCHEDULE_DISPLAY:
        return role == "assistant"
    return False
