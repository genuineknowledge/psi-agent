from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.ai import Ai
from psi_agent.session import Session

# --- Request/Response types ---


@dataclass
class AiCreateRequest:
    provider: str
    model: str
    api_key: str
    base_url: str
    id: str = ""


@dataclass
class AiInfo:
    id: str
    socket: str
    provider: str
    model: str


@dataclass
class SessionCreateRequest:
    ai_id: str
    workspace: str = ""
    id: str = ""


@dataclass
class SessionInfo:
    id: str
    ai_id: str
    workspace: str
    channel_socket: str


@dataclass
class DeleteResponse:
    id: str
    status: str = "stopped"


# --- Helpers ---


def _new_uuid() -> str:
    return uuid.uuid4().hex


def _socket_path(prefix: str, kind: str, entity_id: str) -> str:
    if sys.platform == "win32":
        return rf"\\.\pipe\{prefix}\{kind}\{entity_id}"
    return f"/tmp/{prefix}/{kind}/{entity_id}.sock"


async def _ensure_socket_dir(socket: str) -> None:
    if sys.platform != "win32":
        await anyio.Path(socket).parent.mkdir(parents=True, exist_ok=True)


async def _wait_socket(path: str, timeout_sec: float = 10.0) -> None:
    if sys.platform == "win32":
        await anyio.sleep(0.3)
        return
    deadline = anyio.current_time() + timeout_sec
    sock = anyio.Path(path)
    while anyio.current_time() < deadline:
        if await sock.exists():
            await anyio.sleep(0.3)
            return
        await anyio.sleep(0.1)
    raise TimeoutError(f"Socket '{path}' not created within {timeout_sec}s")


# --- AIManager ---


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

    async def create(self, req: AiCreateRequest) -> AiInfo:
        ai_id = req.id or _new_uuid()
        async with self._lock:
            logger.debug(f"AIManager: acquired lock for create '{ai_id}'")
            if ai_id in self._entries:
                raise ValueError(f"AI '{ai_id}' already exists")
            socket = _socket_path(self._prefix, "ais", ai_id)
            await _ensure_socket_dir(socket)
            ai = Ai(
                session_socket=socket,
                provider=req.provider,
                model=req.model,
                api_key=req.api_key,
                base_url=req.base_url,
            )
            scope = anyio.CancelScope()

            async def _run_ai() -> None:
                with scope:
                    await ai.run()

            logger.debug(f"AIManager: starting AI '{ai_id}' task")
            self._tg.start_soon(_run_ai)
            self._entries[ai_id] = _AiEntry(scope=scope, socket=socket, provider=req.provider, model=req.model)
        await _wait_socket(socket)
        logger.info(f"AI '{ai_id}' created on {socket}")
        return AiInfo(id=ai_id, socket=socket, provider=req.provider, model=req.model)

    async def delete(self, ai_id: str) -> DeleteResponse:
        async with self._lock:
            logger.debug(f"AIManager: acquired lock for delete '{ai_id}'")
            if ai_id not in self._entries:
                raise LookupError(f"AI '{ai_id}' not found")
            entry = self._entries.pop(ai_id)
            entry.scope.cancel()
            logger.info(f"AI '{ai_id}' deleted")
            return DeleteResponse(id=ai_id)

    async def list_all(self) -> list[AiInfo]:
        return [
            AiInfo(id=aid, socket=e.socket, provider=e.provider, model=e.model)
            for aid, e in list(self._entries.items())
        ]

    def get_socket(self, ai_id: str) -> str:
        if ai_id not in self._entries:
            raise LookupError(f"AI '{ai_id}' not found")
        return self._entries[ai_id].socket

    def has(self, ai_id: str) -> bool:
        return ai_id in self._entries


# --- SessionManager ---


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

    async def create(self, req: SessionCreateRequest) -> SessionInfo:
        if not self._aim.has(req.ai_id):
            raise LookupError(f"AI '{req.ai_id}' not found")
        session_id = req.id or _new_uuid()
        workspace = req.workspace or os.getcwd()
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for create '{session_id}'")
            if session_id in self._entries:
                raise ValueError(f"Session '{session_id}' already exists")
            ai_socket = self._aim.get_socket(req.ai_id)
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
                with scope:
                    await sess.run()

            logger.debug(f"SessionManager: starting session '{session_id}' task")
            self._tg.start_soon(_run_session)
            self._entries[session_id] = _SessionEntry(
                scope=scope,
                channel_socket=channel_socket,
                ai_id=req.ai_id,
                workspace=workspace,
            )
        await _wait_socket(channel_socket)
        logger.info(f"Session '{session_id}' created on {channel_socket} -> AI '{req.ai_id}'")
        return SessionInfo(id=session_id, ai_id=req.ai_id, workspace=workspace, channel_socket=channel_socket)

    async def delete(self, session_id: str) -> DeleteResponse:
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for delete '{session_id}'")
            if session_id not in self._entries:
                raise LookupError(f"Session '{session_id}' not found")
            entry = self._entries.pop(session_id)
            entry.scope.cancel()
            logger.info(f"Session '{session_id}' deleted")
            return DeleteResponse(id=session_id)

    async def list_all(self) -> list[SessionInfo]:
        return [
            SessionInfo(id=sid, ai_id=e.ai_id, workspace=e.workspace, channel_socket=e.channel_socket)
            for sid, e in list(self._entries.items())
        ]

    def get_channel_socket(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session '{session_id}' not found")
        return self._entries[session_id].channel_socket

    def has(self, session_id: str) -> bool:
        return session_id in self._entries

    def get_workspace(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session '{session_id}' not found")
        return self._entries[session_id].workspace
