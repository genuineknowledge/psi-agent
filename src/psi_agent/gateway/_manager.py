"""Shared helpers for gateway managers."""

from __future__ import annotations

import sys
import uuid

import aiohttp
import anyio
from loguru import logger


def _new_uuid() -> str:
    return uuid.uuid4().hex


async def _noop() -> None:
    """No-op async callable, used as default for persist callbacks."""
    pass


def _socket_path(prefix: str, kind: str, entity_id: str) -> str:
    if sys.platform == "win32":
        return rf"\\.\pipe\{prefix}\{kind}\{entity_id}"
    return f"/tmp/{prefix}/{kind}/{entity_id}.sock"


async def _ensure_socket_dir(socket: str) -> None:
    if sys.platform != "win32":
        await anyio.Path(socket).parent.mkdir(parents=True, exist_ok=True)


async def _remove_socket(path: str) -> None:
    """Best-effort removal of a leftover socket file (no-op on Windows / if absent)."""
    if sys.platform == "win32":
        return
    try:
        await anyio.Path(path).unlink(missing_ok=True)
        logger.debug(f"Removed socket file {path!r}")
    except OSError as e:
        logger.warning(f"Failed to remove socket file {path!r}: {e!r}")


async def _wait_socket(path: str, timeout_sec: float = 30.0) -> None:
    if sys.platform == "win32":
        connector: aiohttp.BaseConnector = aiohttp.NamedPipeConnector(path=path)
        kind = "Named Pipe"
    else:
        connector = aiohttp.UnixConnector(path=path)
        kind = "Unix socket"
    logger.debug(f"Waiting for {kind} {path!r} to become ready (timeout={timeout_sec}s)")
    deadline = anyio.current_time() + timeout_sec
    session = aiohttp.ClientSession(connector=connector)
    try:
        while anyio.current_time() < deadline:
            try:
                async with session.get("http://localhost/") as _resp:
                    pass
                logger.debug(f"{kind} {path!r} is ready")
                return
            except Exception:
                await anyio.sleep(0.1)
        raise TimeoutError(f"{kind} {path!r} not ready within {timeout_sec}s")
    finally:
        with anyio.CancelScope(shield=True):
            await session.close()
