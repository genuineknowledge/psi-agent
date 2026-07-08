from __future__ import annotations

import json

import anyio
from loguru import logger

# The heartbeat schedule has been removed, but old sessions still carry its
# turns in their history: a ``# Heartbeat Task`` prompt and a ``HEARTBEAT_OK``
# reply. Filter them out so they never resurface in the chat UI on reload.
_HEARTBEAT_REPLY = "HEARTBEAT_OK"
_HEARTBEAT_PROMPT_PREFIX = "# Heartbeat Task"
_HEARTBEAT_WRAPPERS = ("**", "__", "~~", "`")
_HEARTBEAT_SUFFIXES = ("", ".", "!", "-", "---", "!!!")


def _is_heartbeat_message(role: str, text: str) -> bool:
    """Return ``True`` for a message that is part of a heartbeat self-check turn."""
    stripped = text.strip()
    if role == "user":
        return stripped.startswith(_HEARTBEAT_PROMPT_PREFIX)
    # assistant: tolerate markdown wrappers and trailing punctuation the model
    # occasionally added around the bare token.
    for wrapper in _HEARTBEAT_WRAPPERS:
        if len(stripped) >= 2 * len(wrapper) and stripped.startswith(wrapper) and stripped.endswith(wrapper):
            stripped = stripped[len(wrapper) : -len(wrapper)].strip()
            break
    return any(stripped == _HEARTBEAT_REPLY + suffix for suffix in _HEARTBEAT_SUFFIXES)


class HistoryManager:
    async def get(self, workspace: str, session_id: str) -> list[dict[str, str]]:
        path = anyio.Path(workspace) / "histories" / f"{session_id}.jsonl"
        messages: list[dict[str, str]] = []
        try:
            content = await path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"No history file for session {session_id!r} at {path!r}")
            return messages
        except OSError as e:
            logger.warning(f"Failed to read history for session {session_id!r}: {e!r}")
            return messages
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            text = msg.get("content", "")
            if not isinstance(text, str) or not text:
                continue
            # Hide leftover heartbeat turns from removed heartbeat schedules.
            if _is_heartbeat_message(role, text):
                continue
            messages.append({"role": role, "text": text})
        logger.debug(f"History for session {session_id!r}: {len(messages)} displayable message(s)")
        return messages
