from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._manager import (
    _ensure_socket_dir,
    _new_uuid,
    _noop,
    _remove_socket,
    _socket_path,
    _wait_socket,
)
from psi_agent.gateway._router_manager import RouterManager
from psi_agent.session import Session


@dataclass
class SessionInfo:
    id: str
    backend_type: str
    backend_id: str
    workspace: str
    channel_socket: str

    @property
    def ai_id(self) -> str:
        """Compatibility alias for clients that still create direct-AI sessions."""
        return self.backend_id


@dataclass
class _SessionEntry:
    scope: anyio.CancelScope
    info: SessionInfo


@dataclass
class SessionManager:
    _aim: AIManager
    _prefix: str
    _tg: Any  # anyio.TaskGroup (ty不识别的第三方类型)
    _rm: RouterManager | None = None
    _entries: dict[str, _SessionEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _persist: Callable[[], Awaitable[None]] = _noop

    async def create(
        self,
        backend_type: str = "ai",
        backend_id: str = "",
        *,
        ai_id: str = "",
        id: str = "",
        workspace: str = "",
    ) -> SessionInfo:
        session_id = id or _new_uuid()
        workspace = workspace or os.getcwd()
        backend_id = backend_id or ai_id
        upstream_socket = self.resolve_backend_socket(backend_type, backend_id)
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for create {session_id!r}")
            if session_id in self._entries:
                raise ValueError(f"Session {session_id!r} already exists")
            channel_socket = _socket_path(self._prefix, "channels", session_id)
            await _ensure_socket_dir(channel_socket)
            sess = Session(
                workspace=workspace,
                channel_socket=channel_socket,
                ai_socket=upstream_socket,
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
                    await self._persist()

            logger.debug(f"SessionManager: starting session {session_id!r} task")
            self._tg.start_soon(_run_session)
            info = SessionInfo(session_id, backend_type, backend_id, workspace, channel_socket)
            self._entries[session_id] = _SessionEntry(scope=scope, info=info)
        try:
            await _wait_socket(info.channel_socket)
        except Exception:
            logger.warning(f"Session {session_id!r} did not become ready, rolling back")
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(session_id, None)
                    scope.cancel()
                    await _remove_socket(info.channel_socket)
                await self._persist()
            raise
        await self._persist()
        logger.info(f"Session {session_id!r} created on {info.channel_socket} -> {backend_type} {backend_id!r}")
        return info

    def resolve_backend_socket(self, backend_type: str, backend_id: str) -> str:
        if backend_type == "ai":
            return self._aim.get_socket(backend_id)
        if backend_type == "router":
            if self._rm is None:
                raise LookupError("Router manager is not configured")
            return self._rm.get_socket(backend_id)
        raise ValueError("backend_type must be either 'ai' or 'router'")

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for delete {session_id!r}")
            if session_id not in self._entries:
                raise LookupError(f"Session {session_id!r} not found")
            entry = self._entries.pop(session_id)
            entry.scope.cancel()
            await _remove_socket(entry.info.channel_socket)
        await self._persist()
        logger.info(f"Session {session_id!r} deleted")

    async def list_all(self) -> list[SessionInfo]:
        return [e.info for e in list(self._entries.values())]

    def get_socket(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].info.channel_socket

    def has(self, session_id: str) -> bool:
        return session_id in self._entries

    def get_workspace(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].info.workspace
