"""Shared types and helpers for AIManager and SessionManager."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass

import anyio


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
