from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._manager import _ensure_socket_dir, _new_uuid, _noop, _remove_socket, _socket_path, _wait_socket
from psi_agent.router import Router


async def _run_router_service(
    *,
    session_socket: str,
    mode: str,
    router_socket: str,
    upstreams: tuple[tuple[str, str], ...],
    router_timeout: float | None,
    target_timeout: float | None,
    max_context_chars: int,
) -> None:
    router = Router(
        session_socket=session_socket,
        mode=mode,
        router_socket=router_socket,
        upstream=list(upstreams),
        router_timeout=router_timeout,
        target_timeout=target_timeout,
        max_context_chars=max_context_chars,
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
    mode: str
    router_ai_id: str
    upstreams: tuple[RouterUpstreamInfo, ...]
    router_timeout: float | None
    target_timeout: float | None
    max_context_chars: int


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
        mode: str,
        router_ai_id: str,
        upstreams: Sequence[RouterUpstreamInfo],
        *,
        router_timeout: float | None = None,
        target_timeout: float | None = None,
        max_context_chars: int = 12_000,
        id: str = "",
    ) -> RouterInfo:
        router_id = id or _new_uuid()
        if not isinstance(mode, str):
            raise ValueError("mode must be 'routing' or 'aggregation'")
        if not isinstance(name, str) or not isinstance(router_ai_id, str):
            raise ValueError("name and router_ai_id must be non-empty")
        if any(not isinstance(item.ai_id, str) or not isinstance(item.description, str) for item in upstreams):
            raise ValueError("upstreams must contain non-empty ai_id and description values")
        targets = tuple(RouterUpstreamInfo(item.ai_id.strip(), item.description.strip()) for item in upstreams)
        candidate_ids = [x.ai_id for x in targets]
        normalized_mode = mode.strip()
        normalized_name = name.strip()
        normalized_router_ai_id = router_ai_id.strip()
        if normalized_mode not in {"routing", "aggregation"}:
            raise ValueError("mode must be 'routing' or 'aggregation'")
        if not normalized_name or not normalized_router_ai_id:
            raise ValueError("name and router_ai_id must be non-empty")
        if not targets or any(not x.ai_id or not x.description for x in targets):
            raise ValueError("upstreams must contain non-empty ai_id and description values")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("upstreams contain duplicate ai_id values")
        if normalized_mode == "aggregation" and normalized_router_ai_id in candidate_ids:
            raise ValueError("aggregation router_ai_id must not also be an upstream")
        for field_name, value in (("router_timeout", router_timeout), ("target_timeout", target_timeout)):
            if value is not None and (
                not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError(f"{field_name} must be a finite positive number or None")
        if not isinstance(max_context_chars, int) or isinstance(max_context_chars, bool) or max_context_chars <= 0:
            raise ValueError("max_context_chars must be a positive integer")
        for ai_id in (normalized_router_ai_id, *candidate_ids):
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
                            mode=normalized_mode,
                            router_socket=self._aim.get_socket(normalized_router_ai_id),
                            upstreams=tuple((self._aim.get_socket(item.ai_id), item.description) for item in targets),
                            router_timeout=router_timeout,
                            target_timeout=target_timeout,
                            max_context_chars=max_context_chars,
                        )
                except Exception as exc:
                    logger.error(f"Router {router_id!r} crashed: {exc!r}")
                    async with self._lock:
                        self._entries.pop(router_id, None)
                    await self._persist()

            self._tg.start_soon(run_router)
            info = RouterInfo(
                router_id,
                normalized_name,
                socket,
                normalized_mode,
                normalized_router_ai_id,
                targets,
                router_timeout,
                target_timeout,
                max_context_chars,
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
