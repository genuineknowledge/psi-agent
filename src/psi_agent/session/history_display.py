"""Chat-turn provenance via ``kind`` (OpenAI ``role`` stays wire-compatible).

Finalized protocol (2026-07-17):

- ``kind: "chat"`` — ordinary Channel / Web Console turns (display)
- ``kind: "schedule.silent"`` — schedule trigger input, or silent schedule result
  (never display; schedule *user* rows are always this)
- ``kind: "schedule.display"`` — schedule *assistant* result that should surface
- ``kind: "trigger.silent"`` / ``kind: "trigger.display"`` — event-trigger turns
  (same display rules as schedule.* )
- ``kind: "compacted"`` — compaction summary (system-side; not a chat bubble)

Legacy aliases still accepted when reading JSONL:

- ``chat_type: "common"`` → ``chat``
- ``chat_type: "schedule"`` → ``schedule.silent``
- roles ``user_schedule`` / ``assistant_schedule`` → schedule.silent

AI requests strip display-only keys and rewrite legacy roles via ``messages_for_ai``.

``turn_context`` (2026-07-29) is the same idea applied to volatile text: stored
beside the user message it belongs to, folded into ``content`` only on the way
to the AI, never rendered as part of a chat bubble.
"""

from __future__ import annotations

import re
from typing import Any

from psi_agent._transfer_markers import TRANSFER_MARKER_RE, send_paths

KIND_CHAT = "chat"
KIND_SCHEDULE_SILENT = "schedule.silent"
KIND_SCHEDULE_DISPLAY = "schedule.display"
KIND_TRIGGER_SILENT = "trigger.silent"
KIND_TRIGGER_DISPLAY = "trigger.display"
KIND_COMPACTED = "compacted"

KIND_KEY = "kind"

# Legacy field from the preliminary design (session层设计.txt).
CHAT_TYPE_KEY = "chat_type"
CHAT_TYPE_COMMON = "common"
CHAT_TYPE_SCHEDULE = "schedule"

# Volatile per-turn context (wall-clock time, runtime info) carried alongside
# the user message it belongs to.  Folded into ``content`` only when the turn
# is sent to the AI, so history rows stay byte-identical once written — see
# ``messages_for_ai``.
TURN_CONTEXT_KEY = "turn_context"

_DISPLAY_ONLY_KEYS = frozenset({KIND_KEY, CHAT_TYPE_KEY, TURN_CONTEXT_KEY})

_WIRE_ROLES = frozenset({"system", "user", "assistant", "tool"})

_LEGACY_ROLE_TO_WIRE: dict[str, str] = {
    "user_schedule": "user",
    "assistant_schedule": "assistant",
}

_KNOWN_KINDS = frozenset(
    {
        KIND_CHAT,
        KIND_SCHEDULE_SILENT,
        KIND_SCHEDULE_DISPLAY,
        KIND_TRIGGER_SILENT,
        KIND_TRIGGER_DISPLAY,
        KIND_COMPACTED,
    }
)

# Presentation-only strip of wire transfer markers (Gateway history projection).
# Imported rather than re-declared: whatever the Channel accepts as a marker must
# also be stripped from the bubble and projected as an attachment, or a
# ``[ SEND:… ]`` file gets uploaded while its raw marker stays visible in the
# transcript. See ``psi_agent._transfer_markers``.
_TRANSFER_MARKER_RE = TRANSFER_MARKER_RE


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
    """Project history for the AI backend.

    - Strips display-only keys (``kind``, ``chat_type``, ``turn_context``) and
      fixes legacy roles.
    - Folds ``turn_context`` into the message's ``content`` (see
      ``_fold_turn_context``) — the volatile block is stored out-of-band so
      that it lands at the request tail without ever rewriting a stored row.
    - If a ``compacted`` message exists: deletes all messages between
      the first ``system`` (index 0) and the last ``compacted`` (exclusive),
      merges the compaction summary into the system message, and drops the
      ``compacted`` message itself.
    """
    if not messages:
        return []

    compacted_idx: int | None = None
    compacted_content: str = ""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, dict) and msg.get("role") == "compacted":
            compacted_idx = i
            compacted_content = msg.get("content", "")
            break

    if compacted_idx is not None:
        system_idx: int | None = None
        for i, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                system_idx = i
                break

        if system_idx is not None and system_idx < compacted_idx:
            after = messages[compacted_idx + 1 :]
            result: list[dict[str, Any]] = []

            system_msg = messages[system_idx]
            if isinstance(system_msg, dict):
                projected = {k: v for k, v in system_msg.items() if k not in _DISPLAY_ONLY_KEYS}
                projected["role"] = "system"
                projected["content"] = projected.get("content", "") + "\n\n[Compacted History]\n" + compacted_content
                result.append(projected)

            for msg in after:
                if not isinstance(msg, dict):
                    continue
                role = wire_role(msg.get("role"))
                if role is None:
                    continue
                result.append(_project_for_ai(msg, role))
            return result

    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = wire_role(msg.get("role"))
        if role is None:
            continue
        out.append(_project_for_ai(msg, role))
    return out


def _project_for_ai(msg: dict[str, Any], role: str) -> dict[str, Any]:
    """Strip display-only keys, pin ``role``, and fold in ``turn_context``."""
    projected = {k: v for k, v in msg.items() if k not in _DISPLAY_ONLY_KEYS}
    projected["role"] = role
    turn_context = msg.get(TURN_CONTEXT_KEY)
    if isinstance(turn_context, str) and turn_context.strip():
        projected["content"] = _fold_turn_context(projected.get("content"), turn_context)
    return projected


def _fold_turn_context(content: Any, turn_context: str) -> Any:
    """Append the volatile block after ``content``.

    Placed *after* the message body rather than before it so that the stored
    text keeps the position it had when it was written — prefixing would shift
    every byte of the turn, which is exactly what storing the block
    out-of-band is meant to avoid.  Non-string content (multimodal
    block lists) is returned untouched: there is no single place to append to,
    and dropping the block is better than corrupting the blocks.
    """
    if not isinstance(content, str):
        return content
    if not content:
        return turn_context
    return content + "\n\n" + turn_context


def strip_transfer_markers(text: str) -> str:
    """Remove ``[SEND:…]`` / ``[RECV:…]`` from display text (Gateway projection)."""
    cleaned = _TRANSFER_MARKER_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_send_paths(text: str) -> list[str]:
    """Return ``[SEND:…]`` paths in order (stripped); empty / whitespace skipped.

    Delegates to the shared implementation so this projection and the Channel's
    upload scanner can never disagree about what counts as a marker.
    """
    return send_paths(text)


def is_displayable_chat_message(msg: dict[str, Any]) -> bool:
    """Whether Gateway ``/history`` should expose this row as a chat bubble.

    Whitelist by provenance ``kind`` (not content blacklist):

    - ``chat`` user/assistant with non-empty content → yes
    - ``schedule.display`` / ``trigger.display`` assistant with non-empty content → yes
    - ``schedule.silent`` / ``trigger.silent`` / ``compacted`` / tools / system → no
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
    if kind in {KIND_SCHEDULE_DISPLAY, KIND_TRIGGER_DISPLAY}:
        return role == "assistant"
    return False
