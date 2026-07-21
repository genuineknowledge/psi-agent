from __future__ import annotations

import json

import anyio
from loguru import logger

from psi_agent.session.history_display import (
    is_displayable_chat_message,
    message_kind,
    strip_transfer_markers,
    wire_role,
)


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
            if not is_displayable_chat_message(msg):
                continue
            role = wire_role(msg.get("role"))
            text = msg.get("content", "")
            if role not in ("user", "assistant") or not isinstance(text, str):
                continue
            cleaned = strip_transfer_markers(text)
            if not cleaned:
                continue
            row: dict[str, str] = {"role": role, "text": cleaned}
            kind = message_kind(msg)
            if kind != "chat":
                row["kind"] = kind
            messages.append(row)
        logger.debug(f"History for session {session_id!r}: {len(messages)} displayable message(s)")
        return messages
