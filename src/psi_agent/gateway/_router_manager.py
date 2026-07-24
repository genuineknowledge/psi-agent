from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._manager import _ensure_socket_dir, _new_uuid, _noop, _remove_socket, _socket_path, _wait_socket


async def _run_router_service(
    *,
    session_socket: str,
    router_socket: str,
    upstreams: tuple[tuple[str, str], ...],
    default_socket: str,
    router_timeout: float | None,
    router_context_chars: int,
) -> None:
    """Start the Router implementation supplied by the router feature branch.

    The import is intentionally delayed so this Gateway-only branch remains
    importable before the router branch is merged.
    """
    module: Any = __import__("psi_agent.router", fromlist=["Router"])
    router_class = module.Router
    router = router_class(
        session_socket=session_socket,
        router_socket=router_socket,
        upstream=[
            json.dumps({"socket": socket, "description": description}, ensure_ascii=False)
            for socket, description in upstreams
        ],
        default_socket=default_socket,
        router_timeout=router_timeout,
        router_context_chars=router_context_chars,
    )
    await router.run()


@dataclass(frozen=True)
class RouterUpstreamInfo:
    ai_id: str
    description: str


@dataclass(frozen=True)
class RouterInfo:
    id: str
    name: str
    socket: str
    router_ai_id: str
    upstreams: tuple[RouterUpstreamInfo, ...]
    default_ai_id: str
    router_timeout: float | None
    router_context_chars: int


@dataclass
class _RouterEntry:
    scope: anyio.CancelScope
    info: RouterInfo


@dataclass
class RouterManager:
    _aim: AIManager
    _prefix: str
    _tg: Any
    _entries: dict[str, _RouterEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _persist: Callable[[], Awaitable[None]] = _noop

    async def create(
        self,
        name: str,
        router_ai_id: str,
        upstreams: Sequence[RouterUpstreamInfo],
        default_ai_id: str,
        *,
        router_timeout: float | None = None,
        router_context_chars: int = 12_000,
        id: str = "",
    ) -> RouterInfo:
        router_id = id or _new_uuid()
        targets = tuple(RouterUpstreamInfo(x.ai_id.strip(), x.description.strip()) for x in upstreams)
        candidate_ids = [x.ai_id for x in targets]
        if not name.strip() or not router_ai_id.strip():
            raise ValueError("name and router_ai_id must be non-empty")
        if not targets or any(not x.ai_id or not x.description for x in targets):
            raise ValueError("upstreams must contain non-empty ai_id and description values")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("upstreams contain duplicate ai_id values")
        if default_ai_id not in candidate_ids:
            raise ValueError("default_ai_id must identify one of the upstreams")
        if router_context_chars <= 0:
            raise ValueError("router_context_chars must be positive")
        if router_timeout is not None and (not math.isfinite(router_timeout) or router_timeout <= 0):
            raise ValueError("router_timeout must be a finite positive number")
        for ai_id in (router_ai_id, *candidate_ids):
            if not self._aim.has(ai_id):
                raise LookupError(f"AI {ai_id!r} not found")
        async with self._lock:
            if router_id in self._entries:
                raise ValueError(f"Router {router_id!r} already exists")
            socket = _socket_path(self._prefix, "routers", router_id)
            await _ensure_socket_dir(socket)
            scope = anyio.CancelScope()

            async def run_router() -> None:
                try:
                    with scope:
                        await _run_router_service(
                            session_socket=socket,
                            router_socket=self._aim.get_socket(router_ai_id),
                            upstreams=tuple((self._aim.get_socket(item.ai_id), item.description) for item in targets),
                            default_socket=self._aim.get_socket(default_ai_id),
                            router_timeout=router_timeout,
                            router_context_chars=router_context_chars,
                        )
                except Exception as exc:
                    logger.error(f"Router {router_id!r} crashed: {exc!r}")
                    async with self._lock:
                        self._entries.pop(router_id, None)
                    await self._persist()

            self._tg.start_soon(run_router)
            info = RouterInfo(
                router_id,
                name.strip(),
                socket,
                router_ai_id,
                targets,
                default_ai_id,
                router_timeout,
                router_context_chars,
            )
            self._entries[router_id] = _RouterEntry(scope, info)
        try:
            await _wait_socket(socket)
        except Exception:
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(router_id, None)
                    scope.cancel()
                    await _remove_socket(socket)
                await self._persist()
            raise
        await self._persist()
        logger.info(f"Router {router_id!r} created on {socket}")
        return info

    async def delete(self, router_id: str) -> None:
        async with self._lock:
            if router_id not in self._entries:
                raise LookupError(f"Router {router_id!r} not found")
            entry = self._entries.pop(router_id)
            entry.scope.cancel()
            await _remove_socket(entry.info.socket)
        await self._persist()
        logger.info(f"Router {router_id!r} deleted")

    async def list_all(self) -> list[RouterInfo]:
        return [entry.info for entry in list(self._entries.values())]

    def get_socket(self, router_id: str) -> str:
        if router_id not in self._entries:
            raise LookupError(f"Router {router_id!r} not found")
        return self._entries[router_id].info.socket

    def has(self, router_id: str) -> bool:
        return router_id in self._entries

    def get(self, router_id: str) -> RouterInfo:
        if router_id not in self._entries:
            raise LookupError(f"Router {router_id!r} not found")
        return self._entries[router_id].info
