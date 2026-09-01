from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.router import Router, RouterBackendType, RouterUpstream
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._manager import _ensure_socket_dir, _new_uuid, _noop, _remove_socket, _socket_path, _wait_socket


async def _run_router_service(
    *,
    session_socket: str,
    mode: str,
    router_socket: str | None,
    upstreams: tuple[RouterUpstream, ...],
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
    backend_type: RouterBackendType
    backend_id: str
    description: str


@dataclass(frozen=True)
class RouterInfo:
    id: str
    name: str
    socket: str
    mode: str
    router_ai_id: str | None
    upstreams: tuple[RouterUpstreamInfo, ...]
    router_timeout: float | None
    target_timeout: float | None
    max_context_chars: int


@dataclass
class _RouterEntry:
    scope: anyio.CancelScope
    info: RouterInfo


class RouterDependencyError(RuntimeError):
    """A Router cannot be deleted while another Router references it."""


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
        router_ai_id: str | None,
        upstreams: Sequence[RouterUpstreamInfo],
        *,
        router_timeout: float | None = None,
        target_timeout: float | None = None,
        max_context_chars: int = 12_000,
        id: str = "",
    ) -> RouterInfo:
        router_id = id or _new_uuid()
        if not isinstance(mode, str):
            raise ValueError("mode must be 'routing', 'aggregation', or 'fallback'")
        if not isinstance(name, str):
            raise ValueError("name must be non-empty")
        if any(not isinstance(item, RouterUpstreamInfo) for item in upstreams):
            raise ValueError("upstreams must contain RouterUpstreamInfo values")
        if any(
            not isinstance(item.backend_type, str)
            or not isinstance(item.backend_id, str)
            or not isinstance(item.description, str)
            for item in upstreams
        ):
            raise ValueError("upstreams must contain non-empty backend references and descriptions")
        targets = tuple(
            RouterUpstreamInfo(
                backend_type=item.backend_type,
                backend_id=item.backend_id.strip(),
                description=item.description.strip(),
            )
            for item in upstreams
        )
        backend_keys = [(item.backend_type, item.backend_id) for item in targets]
        normalized_mode = mode.strip()
        normalized_name = name.strip()
        if normalized_mode not in {"routing", "aggregation", "fallback"}:
            raise ValueError("mode must be 'routing', 'aggregation', or 'fallback'")
        if not normalized_name:
            raise ValueError("name must be non-empty")
        if normalized_mode == "fallback":
            if router_ai_id is not None:
                raise ValueError("fallback mode requires router_ai_id=None")
            normalized_router_ai_id = None
        else:
            if not isinstance(router_ai_id, str) or not router_ai_id.strip():
                raise ValueError("routing and aggregation modes require a non-empty router_ai_id")
            normalized_router_ai_id = router_ai_id.strip()
        if not targets or any(
            item.backend_type not in {"ai", "router"} or not item.backend_id or not item.description for item in targets
        ):
            raise ValueError("upstreams must contain non-empty typed backend references and descriptions")
        if len(backend_keys) != len(set(backend_keys)):
            raise ValueError("upstreams contain duplicate backend references")
        if normalized_mode == "aggregation" and ("ai", normalized_router_ai_id) in backend_keys:
            raise ValueError("aggregation router_ai_id must not also be an upstream")
        for field_name, value in (("router_timeout", router_timeout), ("target_timeout", target_timeout)):
            if value is not None and (
                not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError(f"{field_name} must be a finite positive number or None")
        if not isinstance(max_context_chars, int) or isinstance(max_context_chars, bool) or max_context_chars <= 0:
            raise ValueError("max_context_chars must be a positive integer")
        async with self._lock:
            if router_id in self._entries:
                raise ValueError(f"Router {router_id!r} already exists")
            if normalized_router_ai_id is not None and not self._aim.has(normalized_router_ai_id):
                raise LookupError(f"AI {normalized_router_ai_id!r} not found")
            router_socket = (
                self._aim.get_socket(normalized_router_ai_id) if normalized_router_ai_id is not None else None
            )
            resolved_upstreams: list[RouterUpstream] = []
            for item in targets:
                if item.backend_type == "ai":
                    if not self._aim.has(item.backend_id):
                        raise LookupError(f"AI {item.backend_id!r} not found")
                    backend_socket = self._aim.get_socket(item.backend_id)
                else:
                    backend_socket = self.get_socket(item.backend_id)
                resolved_upstreams.append((backend_socket, item.description, item.backend_type))
            socket = _socket_path(self._prefix, "routers", router_id)
            await _ensure_socket_dir(socket)
            scope = anyio.CancelScope()

            async def run_router() -> None:
                try:
                    with scope:
                        await _run_router_service(
                            session_socket=socket,
                            mode=normalized_mode,
                            router_socket=router_socket,
                            upstreams=tuple(resolved_upstreams),
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
                id=router_id,
                name=normalized_name,
                socket=socket,
                mode=normalized_mode,
                router_ai_id=normalized_router_ai_id,
                upstreams=targets,
                router_timeout=router_timeout,
                target_timeout=target_timeout,
                max_context_chars=max_context_chars,
            )
            self._entries[router_id] = _RouterEntry(scope=scope, info=info)
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
            dependents = sorted(
                item.info.id
                for item in self._entries.values()
                if item.info.id != router_id
                and any(
                    upstream.backend_type == "router" and upstream.backend_id == router_id
                    for upstream in item.info.upstreams
                )
            )
            if dependents:
                names = ", ".join(repr(item) for item in dependents)
                raise RouterDependencyError(f"Router {router_id!r} is referenced by Router(s): {names}")
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
