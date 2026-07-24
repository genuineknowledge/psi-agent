from __future__ import annotations

import json

import anyio
from loguru import logger

from psi_agent.session.history_display import (
    extract_send_paths,
    is_displayable_chat_message,
    message_kind,
    strip_transfer_markers,
    wire_role,
)


class HistoryManager:
    async def get(self, workspace: str, session_id: str) -> list[dict[str, object]]:
        path = anyio.Path(workspace) / "histories" / f"{session_id}.jsonl"
        messages: list[dict[str, object]] = []
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
            sends = extract_send_paths(text) if role == "assistant" else []
            cleaned = strip_transfer_markers(text)
            # SEND-only assistant turns: fold paths into the previous assistant
            # bubble so spa v1 does not render an empty message.
            if not cleaned and sends:
                if messages and messages[-1].get("role") == "assistant":
                    prev = messages[-1]
                    prev_raw = prev.get("sends")
                    prev_sends = list(prev_raw) if isinstance(prev_raw, list) else []
                    prev_sends.extend(sends)
                    prev["sends"] = prev_sends
                else:
                    messages.append({"role": role, "text": "", "sends": sends})
                continue
            if not cleaned:
                continue
            row: dict[str, object] = {"role": role, "text": cleaned}
            kind = message_kind(msg)
            if kind != "chat":
                row["kind"] = kind
            if sends:
                row["sends"] = sends
            messages.append(row)
        logger.debug(f"History for session {session_id!r}: {len(messages)} displayable message(s)")
        return messages

    async def delete(self, workspace: str, session_id: str) -> None:
        """Remove ``histories/{session_id}.jsonl`` if present (best-effort)."""
        path = anyio.Path(workspace) / "histories" / f"{session_id}.jsonl"
        try:
            await path.unlink()
            logger.info(f"Deleted history file for session {session_id!r} at {path!r}")
        except FileNotFoundError:
            logger.debug(f"No history file to delete for session {session_id!r} at {path!r}")
        except OSError as e:
            logger.warning(f"Failed to delete history for session {session_id!r}: {e!r}")
