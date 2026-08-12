"""FeishuManager — 「飞书会话 → Session」路由表, 复用 SessionManager 动态 spawn。

路由键按会话类型分两支:

* **私聊** (``chat_type`` 为 ``p2p``/缺失) —— 键是发送者 ``open_id``, 每人一个独立 Session,
  于是各自有隔离的历史/workspace/记忆。
* **群聊** (``chat_type`` 为 ``group``/``topic``) —— 键是 ``chat_id``, **整个群共用一个**
  Session。群里所有人对机器人说的话进同一条上下文, 机器人在群里因此有连贯记忆; 群与群、群
  与私聊之间互不串味。

两者都是**动态**的(事先不知道有哪些人/哪些群), 故某键首次路由时按需 spawn 一个 Session。

本模块是 gateway 侧「飞书会话 → Session」的唯一权威 —— channel 只把 ``open_id``/``chat_id``/
``chat_type`` 交给 Gateway 换 socket, 不再自己决定路由键与 ``ai_id``/``workspace``。Session
生命周期仍由 ``SessionManager`` 掌控。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import anyio
from loguru import logger

from psi_agent._feishu_routing import route_key
from psi_agent.gateway._session_manager import SessionManager

_SOCKET_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_open_id(open_id: str) -> str:
    """把 open_id/chat_id 净化成安全的 socket/pipe/path 段。

    飞书 open_id/chat_id 本身即 ``[A-Za-z0-9_]``, 对其是恒等变换; 仅作防御层, 兜住
    union_id/user_id 等意外字符, 避免污染 session_id / workspace 目录名。
    """
    return _SOCKET_UNSAFE.sub("_", open_id)


@dataclass
class FeishuRoute:
    """一条路由记录。群聊只有 ``chat_id``, 私聊只有 ``open_id``, 另一个留空。"""

    open_id: str
    session_id: str
    chat_id: str = ""


@dataclass
class FeishuManager:
    """按 open_id 幂等地把飞书用户路由到各自的 Session。

    ``_ai_id`` / ``_workspace_root`` 是缺省值, 单次 ``route`` 可覆盖。``_routes`` 是内存态
    (路由键 → session_id); 因 session_id 由路由键确定性派生, 重启后经 ``route`` 的 adopt
    分支自愈, 无需额外持久化。
    """

    _sm: SessionManager
    _ai_id: str = ""
    _workspace_root: str = ""
    _routes: dict[str, str] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    def _session_id(self, key: str) -> str:
        """派生确定性 session_id, 加 ``feishu-`` 前缀与 SPA 手建 session 命名空间隔离。

        群聊键 ``chat:<chat_id>`` → ``feishu-chat-<chat_id>``; 私聊 → ``feishu-<open_id>``。
        私聊侧把 ``-`` 转义成 ``_``, 否则某人 open_id 恰为 ``chat-oc_x`` 时会与群 ``oc_x`` 撞成
        同一个 session (陌生人共享上下文的隐私事故)。飞书真实 open_id 不含 ``-``, 这只是防御层。
        """
        if key.startswith("chat:"):
            return f"feishu-chat-{_sanitize_open_id(key.removeprefix('chat:'))}"
        return f"feishu-{_sanitize_open_id(key).replace('-', '_')}"

    def _workspace_for(self, key: str) -> str:
        """每个路由键得到独立子目录 (root 空则以 cwd 为父)。

        群聊 → ``<root>/chat-<chat_id>``, 私聊 → ``<root>/<open_id>`` (``-`` 同样转义,
        与 ``_session_id`` 一致, 免得两个键指到同一个 workspace 目录)。
        """
        root = self._workspace_root or os.getcwd()
        if key.startswith("chat:"):
            return os.path.join(root, f"chat-{_sanitize_open_id(key.removeprefix('chat:'))}")
        return os.path.join(root, _sanitize_open_id(key).replace("-", "_"))

    async def route(
        self,
        open_id: str,
        *,
        chat_id: str = "",
        chat_type: str = "",
        ai_id: str | None = None,
        workspace: str | None = None,
    ) -> tuple[str, str]:
        """幂等地拿到该会话对应 Session 的 (channel_socket, session_id)。

        群聊 (``chat_type`` 为 group/topic 且 ``chat_id`` 非空) 按 ``chat_id`` 路由——整群
        共用一个 Session; 其余按发送者 ``open_id`` 路由。首次见到某键时按需 spawn 一个
        Session; 之后命中缓存或 adopt 已存在 Session。``ai_id`` 最终为空时抛 ``ValueError``
        (由 handler 转 400); 私聊而 ``open_id`` 为空时同样抛 ``ValueError`` (群聊不要求)。
        """
        key = route_key(open_id, chat_id, chat_type)
        if not key:
            raise ValueError("open_id must not be empty")
        sid = self._session_id(key)
        async with self._lock:
            logger.debug(f"FeishuManager: acquired lock for route {key!r}")
            # 命中路由表且 Session 仍活 → 直接复用。
            cached = self._routes.get(key)
            if cached is not None and self._sm.has(cached):
                return self._sm.get_socket(cached), cached

            # 路由表未命中但 Session 已存在 (重启后被 state 恢复, 或 SPA 侧同名建过) → adopt。
            if self._sm.has(sid):
                self._routes[key] = sid
                logger.debug(f"FeishuManager: adopted existing session {sid!r} for {key!r}")
                return self._sm.get_socket(sid), sid

            resolved_ai = ai_id or self._ai_id
            if not resolved_ai:
                raise ValueError("no ai_id: set Gateway --feishu-ai-id or pass ai_id in the request")
            ws = workspace or self._workspace_for(key)
            await anyio.Path(ws).mkdir(parents=True, exist_ok=True)

            try:
                # agent omitted → SessionManager applies Gateway --default-agent
                info = await self._sm.create(ai_id=resolved_ai, id=sid, workspace=ws)
                socket = info.channel_socket
            except ValueError as e:
                # 并发竞态: 另一路已抢先建同名 session (锁内理论不会, 防御性兜底)。
                if "already exists" not in str(e):
                    raise
                logger.debug(f"FeishuManager: session {sid!r} raced, fetching socket")
                socket = self._sm.get_socket(sid)

            self._routes[key] = sid
            logger.info(f"FeishuManager: routed {key!r} -> session {sid!r} (workspace={ws!r})")
            return socket, sid

    def list_routes(self) -> list[FeishuRoute]:
        """列出所有路由。群聊记录填 ``chat_id`` 留空 ``open_id``, 私聊反之。"""
        out: list[FeishuRoute] = []
        for key, sid in self._routes.items():
            if key.startswith("chat:"):
                out.append(FeishuRoute(open_id="", chat_id=key.removeprefix("chat:"), session_id=sid))
            else:
                out.append(FeishuRoute(open_id=key, chat_id="", session_id=sid))
        return out
