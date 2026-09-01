from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent.ai import Ai
from psi_agent.runtime._manager import (
    _ensure_socket_dir,
    _new_uuid,
    _noop,
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
    api_key: str
    base_url: str
    max_context_tokens: int = -1
    """Prompt token threshold that triggers compaction.

    ``-1`` keeps ``Ai``'s own resolution (``PSI_MAX_CONTEXT_TOKENS`` env var,
    else 100K); ``0`` disables compaction.  Defaulted so state snapshots
    written before this field existed still restore.
    """


def _key_as_is(api_key: str, base_url: str) -> str:
    """默认不改写。用户自填的 key 走这条路, 原样交给 AI 层。"""
    _ = base_url
    return api_key


@dataclass
class _AiEntry:
    scope: anyio.CancelScope
    info: AiInfo


@dataclass
class AIManager:
    _prefix: str
    _tg: Any  # anyio.TaskGroup (ty不识别的第三方类型)
    _entries: dict[str, _AiEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _persist: Callable[[], Awaitable[None]] = _noop
    _resolve_key: Callable[[str, str], str] = _key_as_is
    """``(api_key, base_url) -> 真正交给 AI 层的 key``。

    免费模型走的是云端转发, SPA 填的是哨兵值, 真 token 由 Gateway 持有 ——
    这个钩子就是哨兵换 token 的地方 (接线见 ``gateway/__init__.py``)。

    ** 只作用于交给 ``Ai`` 的那一份 **。``AiInfo.api_key`` 仍是哨兵, 所以
    token 不进 ``state/latest.json`` (那里的 api_key 是明文), 也不经 ``/ais``
    下发给 SPA。默认实现原样返回, 用户自填的 key 不受影响。
    """

    async def create(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        *,
        id: str = "",
        max_context_tokens: int = -1,
    ) -> AiInfo:
        want_key = self._config_key(provider, model, api_key, base_url)
        explicit_id = id.strip()
        ai_id = explicit_id or _new_uuid()
        async with self._lock:
            logger.debug(f"AIManager: acquired lock for create {ai_id!r}")
            if ai_id in self._entries:
                raise ValueError(f"AI {ai_id!r} already exists")
            # No explicit id: reuse an already-running identical config (dedupe).
            # Explicit id (Session revive) may still create a second instance with
            # the same provider/model/key so the Session keeps its backend_id.
            if not explicit_id:
                for entry in self._entries.values():
                    info = entry.info
                    if self._config_key(info.provider, info.model, info.api_key, info.base_url) == want_key:
                        logger.info(
                            f"AI create: reusing identical config as {info.id!r} "
                            f"(provider={provider!r} model={model!r})"
                        )
                        return info
            socket = _socket_path(self._prefix, "ais", ai_id)
            await _ensure_socket_dir(socket)
            info = AiInfo(
                id=ai_id,
                socket=socket,
                provider=provider,
                model=model,
                # ** 存入参, 不存解析后的值 **: 解析后可能是登录 token, 而这个
                # 对象会进快照 (明文) 也会经 /ais 下发给 SPA。
                api_key=api_key,
                base_url=base_url,
                max_context_tokens=max_context_tokens,
            )
            scope = self._spawn(info)
            self._entries[ai_id] = _AiEntry(scope=scope, info=info)
        try:
            await _wait_socket(info.socket)
        except Exception:
            logger.warning(f"AI {ai_id!r} did not become ready, rolling back")
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(ai_id, None)
                    scope.cancel()
                    await _remove_socket(info.socket)
                await self._persist()
            raise
        await self._persist()
        logger.info(f"AI {ai_id!r} created on {info.socket}")
        return info

    def _spawn(self, info: AiInfo) -> anyio.CancelScope:
        """起一个 AI 子任务并返回它的取消域。调用方须已持锁。

        ``create`` 与 ``refresh_where`` 共用 —— key 解析只在这一处发生, 不会出现
        「新建时替换了、重建时忘了替换」这种半边生效。
        """
        ai = Ai(
            session_socket=info.socket,
            provider=info.provider,
            model=info.model,
            # 唯一可能是真 token 的一份, 只活在这个 Ai 实例里。
            api_key=self._resolve_key(info.api_key, info.base_url),
            base_url=info.base_url,
            max_context_tokens=info.max_context_tokens,
        )
        scope = anyio.CancelScope()
        ai_id = info.id

        async def _run_ai() -> None:
            try:
                with scope:
                    await ai.run()
            except Exception as e:
                logger.error(f"AI {ai_id!r} crashed: {e!r}")
                async with self._lock:
                    self._entries.pop(ai_id, None)
                await self._persist()

        logger.debug(f"AIManager: starting AI {ai_id!r} task")
        self._tg.start_soon(_run_ai)
        return scope

    @staticmethod
    def _config_key(provider: str, model: str, api_key: str, base_url: str) -> tuple[str, str, str, str]:
        return (provider, model, api_key, base_url.rstrip("/"))

    async def delete(self, ai_id: str) -> None:
        async with self._lock:
            logger.debug(f"AIManager: acquired lock for delete {ai_id!r}")
            if ai_id not in self._entries:
                raise LookupError(f"AI {ai_id!r} not found")
            entry = self._entries.pop(ai_id)
            entry.scope.cancel()
            await _remove_socket(entry.info.socket)
        await self._persist()
        logger.info(f"AI {ai_id!r} deleted")

    async def refresh_where(self, predicate: Callable[[AiInfo], bool]) -> list[str]:
        """原地重建匹配的 socket, 返回被重建的 id。

        ** 为什么需要它 **: 交给 ``Ai`` 的 key 在 socket 构造时就定了 (AI 层把它
        放进 ``app["api_key"]``), 而 ``AiInfo.api_key`` 存的是哨兵 —— 所以
        ``_config_key`` 看不见 token 变化, 换了登录态不会自然拉起新 socket。
        登录/登出时显式调这个方法, 下一次对话才带上新 token。

        ** 原地重建, 不是删了重加 **: ``AiInfo`` 一个字段都不变, 所以模型列表、
        Session 的 ``backend_id``、快照全都不动 —— 用户看不到模型消失又出现。
        变的只有 ``Ai`` 手里那份 key, 它会走 ``_resolve_key`` 重新解析一次。
        """
        async with self._lock:
            targets = [e.info for e in list(self._entries.values()) if predicate(e.info)]
            for info in targets:
                old = self._entries.pop(info.id, None)
                if old is None:
                    continue
                old.scope.cancel()
                await _remove_socket(info.socket)
                await _ensure_socket_dir(info.socket)
                self._entries[info.id] = _AiEntry(
                    scope=self._spawn(info),
                    # 同一个 AiInfo 原样放回: 外部看到的一切都没变。
                    info=info,
                )
        for info in targets:
            try:
                await _wait_socket(info.socket)
            except Exception as e:
                logger.warning(f"AI {info.id!r} 重建后未就绪: {e!r}")
        if targets:
            logger.info(f"登录态变化, 重建 {len(targets)} 个 AI socket: {[t.id for t in targets]}")
        return [t.id for t in targets]

    async def list_all(self) -> list[AiInfo]:
        return [e.info for e in list(self._entries.values())]

    def get_socket(self, ai_id: str) -> str:
        if ai_id in self._entries:
            return self._entries[ai_id].info.socket
        return _socket_path(self._prefix, "ais", ai_id)

    def has(self, ai_id: str) -> bool:
        return ai_id in self._entries
