from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._manager import (
    _ensure_socket_dir,
    _new_uuid,
    _remove_socket,
    _socket_path,
    _wait_socket,
)
from psi_agent.session import Session


@dataclass
class SessionInfo:
    id: str
    ai_id: str
    workspace: str
    channel_socket: str


@dataclass
class _SessionEntry:
    scope: anyio.CancelScope
    channel_socket: str
    ai_id: str
    workspace: str


@dataclass
class SessionManager:
    _aim: AIManager
    _prefix: str
    _tg: Any  # anyio.TaskGroup (ty不识别的第三方类型)
    _entries: dict[str, _SessionEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    async def create(
        self,
        ai_id: str,
        *,
        id: str = "",
        workspace: str = "",
    ) -> SessionInfo:
        if not self._aim.has(ai_id):
            raise LookupError(f"AI '{ai_id}' not found")
        session_id = id or _new_uuid()
        workspace = workspace or str(Path.cwd())
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for create {session_id!r}")
            if session_id in self._entries:
                raise ValueError(f"Session {session_id!r} already exists")
            ai_socket = self._aim.get_socket(ai_id)
            channel_socket = _socket_path(self._prefix, "channels", session_id)
            await _ensure_socket_dir(channel_socket)
            sess = Session(
                workspace=workspace,
                channel_socket=channel_socket,
                ai_socket=ai_socket,
                session_id=session_id,
            )
            scope = anyio.CancelScope()

            async def _run_session() -> None:
                try:
                    with scope:
                        await sess.run()
                except Exception as e:
                    logger.error(f"Session {session_id!r} crashed: {e!r}")
                    async with self._lock:
                        self._entries.pop(session_id, None)

            logger.debug(f"SessionManager: starting session {session_id!r} task")
            self._tg.start_soon(_run_session)
            self._entries[session_id] = _SessionEntry(
                scope=scope,
                channel_socket=channel_socket,
                ai_id=ai_id,
                workspace=workspace,
            )
        try:
            await _wait_socket(channel_socket)
        except Exception:
            logger.warning(f"Session {session_id!r} did not become ready, rolling back")
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(session_id, None)
                    scope.cancel()
                    await _remove_socket(channel_socket)
            raise
        logger.info(f"Session {session_id!r} created on {channel_socket} -> AI '{ai_id}'")
        return SessionInfo(id=session_id, ai_id=ai_id, workspace=workspace, channel_socket=channel_socket)

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for delete {session_id!r}")
            if session_id not in self._entries:
                raise LookupError(f"Session {session_id!r} not found")
            entry = self._entries.pop(session_id)
            entry.scope.cancel()
            await _remove_socket(entry.channel_socket)
            logger.info(f"Session {session_id!r} deleted")

    async def list_all(self) -> list[SessionInfo]:
        return [
            SessionInfo(id=sid, ai_id=e.ai_id, workspace=e.workspace, channel_socket=e.channel_socket)
            for sid, e in list(self._entries.items())
        ]

    def get_socket(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].channel_socket

    def has(self, session_id: str) -> bool:
        return session_id in self._entries

    def get_workspace(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].workspace
