from __future__ import annotations

import json
from typing import Any

import anyio
from loguru import logger

from psi_agent.session.history_display import (
    ROLE_USER_FEEDBACK,
    is_displayable_chat_message,
)


class HistoryManager:
    async def get(self, workspace: str, session_id: str) -> list[dict[str, Any]]:
        path = anyio.Path(workspace) / "histories" / f"{session_id}.jsonl"
        messages: list[dict[str, Any]] = []
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
            if msg.get("role") == ROLE_USER_FEEDBACK:
                # Stamp the preceding assistant bubble for thumbs UI; never expose as its own row.
                kind = msg.get("feedback", "")
                if kind in ("up", "down") and messages and messages[-1].get("role") == "assistant":
                    messages[-1]["feedback"] = kind
                continue
            if not is_displayable_chat_message(msg):
                continue
            role = msg.get("role", "")
            text = msg.get("content", "")
            # is_displayable_chat_message already requires non-empty str content
            row: dict[str, Any] = {"role": str(role), "text": str(text)}
            if role == "assistant":
                row["feedback"] = ""
            messages.append(row)
        logger.debug(f"History for session {session_id!r}: {len(messages)} displayable message(s)")
        return messages
