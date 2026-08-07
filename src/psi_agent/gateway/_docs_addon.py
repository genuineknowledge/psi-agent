"""DocsAddonManager — 「云文档小组件会话 → Session」路由表, 复用 SessionManager 动态 spawn。

飞书云文档小组件 (docs add-on) 是跑在文档里的一个 iframe 块。用户在小组件里打字, 小组件
把话 POST 给本模块换到一个 Session, 再走 gateway 既有的 ``POST /sessions/{id}/chat``
流式拿回答 —— 也就是说本模块只管**路由**, 不碰对话本身。

路由键是 ``(doc_token, user_id)`` 两者的组合, 于是:

* 同一个人在**不同文档**里各有独立上下文 —— 小组件是文档的一部分, 在 A 文档聊的内容不该
  漏进 B 文档。
* 同一篇文档里**不同人**各有独立上下文 —— 文档常是多人协作的, 一篇文档一个共享 session
  会让同事互相看见对方的提问。

这与 ``_feishu_manager`` 的取舍**刻意相反**(群聊整群共用一个 Session): 群聊里大家本就在
同一场对话中, 而文档小组件里各人是各自在用工具。

## 身份不可信

``user_id`` 来自小组件前端的 ``Service.User.getUserId()``, 是**客户端自报**的值, 服务端
无从验证 —— 任何人构造一个 HTTP 请求都能填别人的 id。所以:

* ``user_id`` **只用于会话隔离**, 不是认证凭据, 不能拿它做授权判断。
* 访问控制靠 ``_token`` (预共享密钥, 小组件设置面板里填)。没配 token 就整个端点不可用,
  免得裸奔在公网上。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

import anyio
from loguru import logger

from psi_agent.gateway._session_manager import SessionManager

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# doc_token / user_id 原样进 session_id 会太长 (doc_token 27 字符 + open_id 27 字符 + 前缀,
# 在 Windows 命名管道和 Unix socket 路径长度上都危险), 故各取哈希前若干位。
_HASH_LEN = 12


def _sanitize(value: str) -> str:
    """把 token/id 净化成安全的 socket/pipe/path 段(防御层, 正常输入是恒等变换)。"""
    return _UNSAFE.sub("_", value)


def _short_hash(value: str) -> str:
    """稳定的短摘要 —— 让 session_id 长度可控且不随平台变化。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_HASH_LEN]


@dataclass
class DocsAddonRoute:
    """一条路由记录。"""

    doc_token: str
    user_id: str
    session_id: str


@dataclass
class DocsAddonManager:
    """按 ``(doc_token, user_id)`` 幂等地把小组件会话路由到各自的 Session。

    ``_ai_id`` / ``_workspace_root`` 是缺省值, 单次 ``route`` 可覆盖。``_token`` 是预共享
    访问密钥, 空则 ``enabled`` 为 False (端点整体拒绝服务)。``_routes`` 是内存态; 因
    session_id 由路由键确定性派生, 重启后经 ``route`` 的 adopt 分支自愈。
    """

    _sm: SessionManager
    _ai_id: str = ""
    _workspace_root: str = ""
    _token: str = ""
    _routes: dict[str, str] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    @property
    def enabled(self) -> bool:
        """没配预共享 token 就不启用 —— 无认证的对话端点不该默默对外开着。"""
        return bool(self._token)

    def check_token(self, presented: str) -> bool:
        """常量时间比对预共享 token, 避免按字节泄漏。"""
        if not self._token:
            return False
        return hmac.compare_digest(self._token, presented or "")

    @staticmethod
    def _route_key(doc_token: str, user_id: str) -> str:
        """路由键: ``<doc_token>|<user_id>``。

        用 ``|`` 分隔 —— 飞书的 doc_token 与 open_id 都是 ``[A-Za-z0-9_]``, 不含 ``|``,
        所以 ("ab", "c") 和 ("a", "bc") 不可能撞成同一个键。
        """
        return f"{doc_token}|{user_id}"

    def _session_id(self, doc_token: str, user_id: str) -> str:
        """派生确定性 session_id, 前缀 ``docsaddon-`` 与飞书/SPA 的命名空间隔离。

        两段各自哈希再拼, 而不是哈希拼接后的整串 —— 这样长度固定且可分辨来源, 同时避免
        原样带上过长的 token 撑爆 socket 路径。
        """
        return f"docsaddon-{_short_hash(doc_token)}-{_short_hash(user_id)}"

    def _workspace_for(self, doc_token: str, user_id: str) -> str:
        """每个 (文档, 人) 得到独立子目录 (root 空则以 cwd 为父)。"""
        root = self._workspace_root or os.getcwd()
        return os.path.join(root, f"docsaddon-{_sanitize(doc_token)}", _sanitize(user_id))

    async def route(
        self,
        doc_token: str,
        user_id: str,
        *,
        ai_id: str | None = None,
        workspace: str | None = None,
    ) -> tuple[str, str]:
        """幂等地拿到该 (文档, 人) 对应 Session 的 (channel_socket, session_id)。

        首次见到某键时按需 spawn 一个 Session; 之后命中缓存或 adopt 已存在 Session。
        ``doc_token`` / ``user_id`` 为空, 或 ``ai_id`` 最终为空时抛 ``ValueError``
        (由 handler 转 400)。
        """
        if not doc_token:
            raise ValueError("doc_token must not be empty")
        if not user_id:
            raise ValueError("user_id must not be empty")

        key = self._route_key(doc_token, user_id)
        sid = self._session_id(doc_token, user_id)
        async with self._lock:
            logger.debug(f"DocsAddonManager: acquired lock for route {key!r}")
            # 命中路由表且 Session 仍活 → 直接复用。
            cached = self._routes.get(key)
            if cached is not None and self._sm.has(cached):
                return self._sm.get_socket(cached), cached

            # 路由表未命中但 Session 已存在 (重启后被 state 恢复) → adopt。
            if self._sm.has(sid):
                self._routes[key] = sid
                logger.debug(f"DocsAddonManager: adopted existing session {sid!r} for {key!r}")
                return self._sm.get_socket(sid), sid

            resolved_ai = ai_id or self._ai_id
            if not resolved_ai:
                raise ValueError("no ai_id: set Gateway --docs-addon-ai-id or pass ai_id in the request")
            ws = workspace or self._workspace_for(doc_token, user_id)
            await anyio.Path(ws).mkdir(parents=True, exist_ok=True)

            try:
                # agent omitted → SessionManager applies Gateway --default-agent
                info = await self._sm.create(ai_id=resolved_ai, id=sid, workspace=ws)
                socket = info.channel_socket
            except ValueError as e:
                # 并发竞态: 另一路已抢先建同名 session (锁内理论不会, 防御性兜底)。
                if "already exists" not in str(e):
                    raise
                logger.debug(f"DocsAddonManager: session {sid!r} raced, fetching socket")
                socket = self._sm.get_socket(sid)

            self._routes[key] = sid
            logger.info(f"DocsAddonManager: routed {key!r} -> session {sid!r} (workspace={ws!r})")
            return socket, sid

    def list_routes(self) -> list[DocsAddonRoute]:
        """列出所有路由。"""
        out: list[DocsAddonRoute] = []
        for key, sid in self._routes.items():
            doc_token, _, user_id = key.partition("|")
            out.append(DocsAddonRoute(doc_token=doc_token, user_id=user_id, session_id=sid))
        return out
