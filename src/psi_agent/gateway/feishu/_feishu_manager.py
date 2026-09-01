"""FeishuManager — 「飞书会话 → Session」路由表, 复用 SessionManager 动态 spawn。

路由键按会话类型分两支:

* **私聊** (``chat_type`` 为 ``p2p``/缺失) —— 键是发送者 ``open_id``, 每人一个独立 Session,
  于是各自有隔离的历史/workspace/记忆。
* **群聊** (``chat_type`` 为 ``group``/``topic``) —— 键是 ``chat_id``, **整个群共用一个**
  Session。群里所有人对机器人说的话进同一条上下文, 机器人在群里因此有连贯记忆; 群与群、群
  与私聊之间互不串味。

两者都是**动态**的(事先不知道有哪些人/哪些群), 故某键首次路由时按需 spawn 一个 Session。

例外是 ``PSI_FEISHU_EXTERNAL_SESSIONS`` 里登记的键 —— 它们的 Session 跑在**本进程之外**
(另一个容器), 本模块只把地址透传给 channel, 不 spawn 也不管生命周期。用途见
``external_sessions``: 给某人真正独立的容器, 换取进程/文件系统级隔离。

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

from psi_agent import _private_space
from psi_agent._feishu_routing import route_key
from psi_agent.runtime._session_manager import SessionManager

_SOCKET_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

_EXTERNAL_ENV_KEY = "PSI_FEISHU_EXTERNAL_SESSIONS"


def external_sessions() -> dict[str, str]:
    """读 ``PSI_FEISHU_EXTERNAL_SESSIONS``: ``<路由键>=<地址>`` 逗号/分号分隔。

    地址是 ``_sockets`` 认得的传输地址, 跨容器场景填 ``http://host:port`` (TCP)。命中的键
    **不 spawn 本进程 Session**, 直接把地址交给 channel —— 那边的 Session 跑在别的容器里,
    有自己的文件系统, 于是本容器的 agent 连它的文件都看不见 (工具层守卫之外的真隔离)。

    形如 ``ou_xxx=http://psi-agent-luolin:8081``。群聊键要写全 ``chat:oc_xxx``。解析失败的
    片段静默跳过 —— 配置错字不该让整个 gateway 起不来, 未命中就退回本进程 spawn 的老路径。

    每次调用都重读环境变量而不缓存: 表极小(个位数条目), 而重读换来的是「改了 env 重启
    进程即生效」这一条最简单的运维语义, 不必再关心某处缓存有没有失效。
    """
    raw = os.environ.get(_EXTERNAL_ENV_KEY, "") or ""
    out: dict[str, str] = {}
    for chunk in raw.replace(";", ",").split(","):
        key, sep, addr = chunk.partition("=")
        key, addr = key.strip(), addr.strip()
        if not sep or not key or not addr:
            continue
        out[key] = addr
    return out


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

    def session_id_for(self, key: str) -> str:
        """派生确定性 session_id, 加 ``feishu-`` 前缀与 SPA 手建 session 命名空间隔离。

        群聊键 ``chat:<chat_id>`` → ``feishu-chat-<chat_id>``; 私聊 → ``feishu-<open_id>``。
        私聊侧把 ``-`` 转义成 ``_``, 否则某人 open_id 恰为 ``chat-oc_x`` 时会与群 ``oc_x`` 撞成
        同一个 session (陌生人共享上下文的隐私事故)。飞书真实 open_id 不含 ``-``, 这只是防御层。

        **公开**是因为网页应用侧要按同一份逻辑建 session/workspace: 重实现一次就会漏掉上面
        那条转义。派生只能有一份, 故对外只暴露本方法, 不暴露拼接细节。
        """
        if key.startswith("chat:"):
            return f"feishu-chat-{_sanitize_open_id(key.removeprefix('chat:'))}"
        return f"feishu-{_sanitize_open_id(key).replace('-', '_')}"

    def _session_id(self, key: str) -> str:
        """内部别名 —— 既有 5 处调用点不动, 实现见 ``session_id_for``。"""
        return self.session_id_for(key)

    @property
    def default_ai_id(self) -> str:
        """机器人侧 ``route()`` 在请求不带 ``ai_id`` 时会用的那个实例 id (即 ``--feishu-ai-id``)。

        **公开是为了让网页应用与机器人共用同一个答案**: ``GET /feishu/defaults`` 读的就是
        这里, 于是两侧的模型出自同一个字段, 而不是各自去 ``GET /ais`` 里挑。读属性而不是
        让路由层碰 ``_ai_id``: 私有字段一旦被外部读, 「缺省值住哪」就有了第二个知情者。

        空串表示部署没配 —— 与 ``route()`` 里 ``resolved_ai`` 为空时抛 ValueError 是同一件
        事的两种表达: 机器人侧当场报错, 网页侧由前端显示成可读提示。
        """
        return self._ai_id

    def workspace_for(self, key: str) -> str:
        """每个路由键得到独立子目录 (root 空则以 cwd 为父)。

        群聊 → ``<root>/chat-<chat_id>``, 私聊 → ``<root>/<open_id>`` (``-`` 同样转义,
        与 ``session_id_for`` 一致, 免得两个键指到同一个 workspace 目录)。

        ``PSI_PRIVATE_OPEN_IDS`` 白名单里的人 → ``<root>/.private/<open_id>``, 工具层
        据此拒绝其他 session 访问 (见 ``psi_agent._private_space``)。群聊不进私密区 ——
        群是多人共用上下文, 放私密区等于把私密资料摊给全群。

        网页应用侧「一个人的多个会话共享一个 workspace」正是靠调本方法实现: 同一个
        ``open_id`` 无论开几个 session, 都落这一个目录。
        """
        root = self._workspace_root or os.getcwd()
        if key.startswith("chat:"):
            return os.path.join(root, f"chat-{_sanitize_open_id(key.removeprefix('chat:'))}")
        if _private_space.is_private_user(key):
            return _private_space.private_dir(root, _sanitize_open_id(key))
        return os.path.join(root, _sanitize_open_id(key).replace("-", "_"))

    def _workspace_for(self, key: str) -> str:
        """内部别名 —— 既有调用点不动, 实现见 ``workspace_for``。"""
        return self.workspace_for(key)

    def is_external(self, open_id: str, *, chat_id: str = "", chat_type: str = "") -> bool:
        """该会话是否由**别的容器**里的 Session 托管 (``PSI_FEISHU_EXTERNAL_SESSIONS`` 命中)。

        供 ``/feishu/route`` 如实告诉 channel: 外部容器有自己的文件系统, 本进程下载的附件
        那边根本看不见 (实测 channel 把文件存到主容器的 ``~/Downloads/.psi/<date>/``, 而
        专用容器里该目录不存在 → agent 报「没收到简历」)。channel 据此改为透传 file_key,
        由真正处理消息的容器自己下载。

        判定只读环境变量, 与 ``route`` 用的是同一份 ``external_sessions()``, 不会出现
        「route 走外部、这里说本地」的分歧。
        """
        key = route_key(open_id, chat_id, chat_type)
        return bool(key) and key in external_sessions()

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

        ``PSI_FEISHU_EXTERNAL_SESSIONS`` 命中的键例外: 直接返回登记地址, 本进程不 spawn。
        """
        key = route_key(open_id, chat_id, chat_type)
        if not key:
            raise ValueError("open_id must not be empty")
        # 外部容器托管的键: 只透传地址。放在锁与 spawn 之前 —— 这类键在本进程既没有
        # Session 也不该建, session_id 报路由键本身派生的值, 免得看着像本地 session。
        external = external_sessions().get(key)
        if external:
            logger.debug(f"FeishuManager: {key!r} handled by external session at {external!r}")
            return external, self._session_id(key)
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
