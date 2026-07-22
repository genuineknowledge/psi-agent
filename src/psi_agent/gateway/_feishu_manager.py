"""FeishuManager — 「飞书 open_id → Session」路由表, 复用 SessionManager 动态 spawn。

飞书机器人对每个飞书用户提供**独立**的沟通渠道: 按发送者 ``open_id`` 把消息路由到各自的
Session。用户是**动态**的(事先不知道有哪些人), 故某用户首次路由时按需 spawn 一个 Session。

本模块是 gateway 侧「open_id → Session」的唯一权威 —— channel 只拿 open_id 问 Gateway 要
socket, 不再自己决定 ``ai_id``/``workspace``。Session 生命周期仍由 ``SessionManager`` 掌控。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import anyio
from loguru import logger

from psi_agent.gateway._session_manager import SessionManager

_SOCKET_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_open_id(open_id: str) -> str:
    """把 open_id 净化成安全的 socket/pipe/path 段。

    飞书 open_id 本身即 ``[A-Za-z0-9_]``, 对其是恒等变换; 仅作防御层, 兜住
    union_id/user_id 等意外字符, 避免污染 session_id / workspace 目录名。
    """
    return _SOCKET_UNSAFE.sub("_", open_id)


@dataclass
class FeishuRoute:
    open_id: str
    session_id: str


@dataclass
class FeishuManager:
    """按 open_id 幂等地把飞书用户路由到各自的 Session。

    ``_ai_id`` / ``_workspace_root`` 是缺省值, 单次 ``route`` 可覆盖。``_routes`` 是内存态
    (open_id → session_id); 因 session_id 由 open_id 确定性派生, 重启后经 ``route`` 的 adopt
    分支自愈, 无需额外持久化。
    """

    _sm: SessionManager
    _ai_id: str = ""
    _workspace_root: str = ""
    _routes: dict[str, str] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    def _session_id(self, open_id: str) -> str:
        """派生确定性 session_id, 加 ``feishu-`` 前缀与 SPA 手建 session 命名空间隔离。"""
        return f"feishu-{_sanitize_open_id(open_id)}"

    def _workspace_for(self, open_id: str) -> str:
        """每个 open_id 得到 ``<root>/<open_id>`` 独立子目录 (root 空则以 cwd 为父)。"""
        root = self._workspace_root or os.getcwd()
        return os.path.join(root, _sanitize_open_id(open_id))

    async def route(
        self,
        open_id: str,
        *,
        ai_id: str | None = None,
        workspace: str | None = None,
    ) -> tuple[str, str]:
        """按 open_id 幂等地拿到其 Session 的 (channel_socket, session_id)。

        首次见到某 open_id 时按需 spawn 一个 Session; 之后命中缓存或 adopt 已存在 Session。
        ``ai_id`` 最终为空时抛 ``ValueError`` (由 handler 转 400)。
        """
        if not open_id:
            raise ValueError("open_id must not be empty")
        sid = self._session_id(open_id)
        async with self._lock:
            # 命中路由表且 Session 仍活 → 直接复用。
            cached = self._routes.get(open_id)
            if cached is not None and self._sm.has(cached):
                return self._sm.get_socket(cached), cached

            # 路由表未命中但 Session 已存在 (重启后被 state 恢复, 或 SPA 侧同名建过) → adopt。
            if self._sm.has(sid):
                self._routes[open_id] = sid
                logger.debug(f"FeishuManager: adopted existing session {sid!r} for open_id={open_id!r}")
                return self._sm.get_socket(sid), sid

            resolved_ai = ai_id or self._ai_id
            if not resolved_ai:
                raise ValueError("no ai_id: set Gateway --feishu-ai-id or pass ai_id in the request")
            ws = workspace or self._workspace_for(open_id)
            await anyio.Path(ws).mkdir(parents=True, exist_ok=True)

            try:
                info = await self._sm.create(ai_id=resolved_ai, id=sid, workspace=ws)
                socket = info.channel_socket
            except ValueError as e:
                # 并发竞态: 另一路已抢先建同名 session (锁内理论不会, 防御性兜底)。
                if "already exists" not in str(e):
                    raise
                logger.debug(f"FeishuManager: session {sid!r} raced, fetching socket")
                socket = self._sm.get_socket(sid)

            self._routes[open_id] = sid
            logger.info(f"FeishuManager: routed open_id={open_id!r} -> session {sid!r} (workspace={ws!r})")
            return socket, sid

    def list_routes(self) -> list[FeishuRoute]:
        return [FeishuRoute(open_id=oid, session_id=sid) for oid, sid in self._routes.items()]
