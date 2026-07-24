from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent._app_paths import default_agent_path, default_workspace_path
from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._manager import (
    _ensure_socket_dir,
    _new_uuid,
    _noop,
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
    """User workspace (open folder)."""

    channel_socket: str
    agent: str = ""
    """Agent package path. Empty string in old snapshots → filled on restore via default."""


@dataclass
class _SessionEntry:
    scope: anyio.CancelScope
    info: SessionInfo


@dataclass
class SessionManager:
    _aim: AIManager
    _prefix: str
    _tg: Any  # anyio.TaskGroup (ty不识别的第三方类型)
    _entries: dict[str, _SessionEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _persist: Callable[[], Awaitable[None]] = _noop
    _default_agent: str = ""
    _default_workspace: str = ""
    _app_data_root: str = ""

    async def create(
        self,
        ai_id: str,
        *,
        id: str = "",
        workspace: str = "",
        agent: str = "",
    ) -> SessionInfo:
        session_id = id or _new_uuid()
        workspace = workspace or self._default_workspace or str(default_workspace_path())
        agent = agent or self._default_agent or str(default_agent_path())
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for create {session_id!r}")
            if session_id in self._entries:
                raise ValueError(f"Session {session_id!r} already exists")
            ai_socket = self._aim.get_socket(ai_id)
            channel_socket = _socket_path(self._prefix, "channels", session_id)
            await _ensure_socket_dir(channel_socket)
            sess = Session(
                workspace=workspace,
                agent=agent,
                channel_socket=channel_socket,
                ai_socket=ai_socket,
                session_id=session_id,
                app_data_root=self._app_data_root,
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
            info = SessionInfo(
                id=session_id,
                ai_id=ai_id,
                workspace=workspace,
                agent=agent,
                channel_socket=channel_socket,
            )
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
        logger.info(
            f"Session {session_id!r} created on {info.channel_socket} "
            f"-> AI '{ai_id}' agent={agent!r} workspace={workspace!r}"
        )
        return info

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

    def get_agent(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].info.agent
