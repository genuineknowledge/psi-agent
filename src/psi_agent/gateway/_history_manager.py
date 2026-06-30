from __future__ import annotations

import json

import anyio
from loguru import logger


class HistoryManager:
    async def get(self, workspace: str, session_id: str) -> list[dict[str, str]]:
        path = anyio.Path(workspace) / "histories" / f"{session_id}.jsonl"
        messages: list[dict[str, str]] = []
        try:
            content = await path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"No history file for session '{session_id}' at {path}")
            return messages
        except OSError as e:
            logger.warning(f"Failed to read history for session '{session_id}': {e}")
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
            if isinstance(text, str) and text:
                messages.append({"role": role, "text": text})
        logger.debug(f"History for session '{session_id}': {len(messages)} displayable message(s)")
        return messages
