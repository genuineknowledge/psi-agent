from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.ai import Ai
from psi_agent.gateway._manager import (
    _ensure_socket_dir,
    _new_uuid,
    _remove_socket,
    _socket_path,
    _wait_socket,
)


@dataclass
class AiInfo:
    id: str
    socket: str
    provider: str
    model: str


@dataclass
class _AiEntry:
    scope: anyio.CancelScope
    socket: str
    provider: str
    model: str


@dataclass
class AIManager:
    _prefix: str
    _tg: Any  # anyio.TaskGroup (ty不识别的第三方类型)
    _entries: dict[str, _AiEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    async def create(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        *,
        id: str = "",
    ) -> AiInfo:
        ai_id = id or _new_uuid()
        async with self._lock:
            logger.debug(f"AIManager: acquired lock for create {ai_id!r}")
            if ai_id in self._entries:
                raise ValueError(f"AI {ai_id!r} already exists")
            socket = _socket_path(self._prefix, "ais", ai_id)
            await _ensure_socket_dir(socket)
            ai = Ai(
                session_socket=socket,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            scope = anyio.CancelScope()

            async def _run_ai() -> None:
                try:
                    with scope:
                        await ai.run()
                except Exception as e:
                    logger.error(f"AI {ai_id!r} crashed: {e!r}")
                    async with self._lock:
                        self._entries.pop(ai_id, None)

            logger.debug(f"AIManager: starting AI {ai_id!r} task")
            self._tg.start_soon(_run_ai)
            self._entries[ai_id] = _AiEntry(scope=scope, socket=socket, provider=provider, model=model)
        try:
            await _wait_socket(socket)
        except Exception:
            logger.warning(f"AI {ai_id!r} did not become ready, rolling back")
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(ai_id, None)
                    scope.cancel()
                    await _remove_socket(socket)
            raise
        logger.info(f"AI {ai_id!r} created on {socket}")
        return AiInfo(id=ai_id, socket=socket, provider=provider, model=model)

    async def delete(self, ai_id: str) -> None:
        async with self._lock:
            logger.debug(f"AIManager: acquired lock for delete {ai_id!r}")
            if ai_id not in self._entries:
                raise LookupError(f"AI {ai_id!r} not found")
            entry = self._entries.pop(ai_id)
            entry.scope.cancel()
            await _remove_socket(entry.socket)
            logger.info(f"AI {ai_id!r} deleted")

    async def list_all(self) -> list[AiInfo]:
        return [
            AiInfo(id=aid, socket=e.socket, provider=e.provider, model=e.model)
            for aid, e in list(self._entries.items())
        ]

    def get_socket(self, ai_id: str) -> str:
        if ai_id not in self._entries:
            raise LookupError(f"AI {ai_id!r} not found")
        return self._entries[ai_id].socket

    def has(self, ai_id: str) -> bool:
        return ai_id in self._entries
