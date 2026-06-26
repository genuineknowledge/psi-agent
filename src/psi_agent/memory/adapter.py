"""Session-level Fusion Memory adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from psi_agent.memory.client import FusionMemoryClient
from psi_agent.memory.config import MemoryConfig
from psi_agent.memory.formatting import format_memory_context
from psi_agent.memory.scope import build_memory_scope


class SessionMemoryAdapter:
    """Fail-open memory integration used by session agents."""

    def __init__(self, config: MemoryConfig) -> None:
        self.config = config
        self.scope = build_memory_scope(config.workspace)
        self.client: FusionMemoryClient | None = None
        self._warned = False

    async def start(self) -> None:
        if not self.config.memory_enabled:
            return
        self.client = FusionMemoryClient(
            self.config.memory_base_url,
            timeout_seconds=self.config.memory_timeout_seconds,
        )
        try:
            await self.client.health()
            logger.info("Fusion Memory enabled.")
        except Exception:
            await self.close()
            self._warn_once("Fusion Memory health check failed; continuing without memory.")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def retrieve_for_turn(self, user_message: dict[str, Any]) -> str | None:
        if not self._can_read():
            return None
        query = _message_text(user_message)
        if not query:
            return None
        assert self.client is not None
        try:
            pack = await self.client.answer_context(
                query,
                self.scope,
                budget={
                    "limit": self.config.memory_retrieval_limit,
                    "allow_cross_session": self.config.memory_allow_cross_session,
                },
            )
            return format_memory_context(pack, max_chars=self.config.memory_inject_max_chars)
        except Exception:
            self._warn_once("Fusion Memory retrieval failed; continuing without memory.")
            return None

    async def record_turn(
        self,
        user_message: dict[str, Any],
        assistant_content: str,
    ) -> None:
        if not self._can_write():
            return
        user_content = _message_text(user_message)
        if not user_content and not assistant_content:
            return
        assert self.client is not None
        now = datetime.now(UTC).isoformat()
        try:
            await self.client.add(
                [
                    {
                        "role": "user",
                        "content": user_content,
                        "timestamp": now,
                        "turn_id": "user",
                    },
                    {
                        "role": "assistant",
                        "content": assistant_content,
                        "timestamp": now,
                        "turn_id": "assistant",
                    },
                ],
                self.scope,
                session_time=now,
                metadata={"source": "psi-agent", "mode": "auto-turn"},
            )
        except Exception:
            self._warn_once("Fusion Memory write failed; continuing without memory.")

    def _can_read(self) -> bool:
        return bool(self.config.memory_enabled and self.config.memory_auto_read and self.client is not None)

    def _can_write(self) -> bool:
        return bool(self.config.memory_enabled and self.config.memory_auto_write and self.client is not None)

    def _warn_once(self, message: str) -> None:
        if not self._warned:
            logger.warning(message)
            self._warned = True
        else:
            logger.debug(message)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content) if content else ""
