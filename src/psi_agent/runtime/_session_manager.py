from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent._workspace_paths import ensure_workspace_dir, is_strictly_under
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._manager import (
    _ensure_socket_dir,
    _new_uuid,
    _noop,
    _remove_socket,
    _socket_path,
    _wait_socket,
)
from psi_agent.runtime._router_manager import RouterManager
from psi_agent.session import Session
from psi_agent.session.schedule_registry import ACTIVATE_ALL


@dataclass
class SessionInfo:
    id: str
    backend_type: str
    backend_id: str
    workspace: str
    """User workspace (open folder). Relative file IO / project files live here."""

    channel_socket: str
    # Step 2: surfaced to REST / state. Empty → Session treats agent ≡ workspace.
    agent: str = ""
    """Agent package path (tools/system). Empty → single-root compat."""

    active_schedules: tuple[str, ...] = ()
    """Schedule names this Session activates (i.e. actually fires); ``("*",)`` = all.

    Activation is a property of **(session x schedule)**: every Session on a
    workspace reads every entry, but each fires only the ones in its own list.
    ``SchedulerManager`` keeps exactly one fully activated (``("*",)``) scheduler
    Session per workspace, and that Session is **entirely hidden** from the SPA
    and from ``state/latest.json`` (filtered out of ``list_all`` by default,
    skipped when persisting) — 刻意为之: it is not a user session, and listing it
    would only invite someone to delete it.
    """

    deactive_schedules: tuple[str, ...] = ()
    """Schedule names excluded from ``active_schedules`` (blacklist, wins over it).

    A wildcard whitelist plus a blacklist is the only way to say "all of these
    except a few": a whitelist is an enumeration and cannot cover a ``TASK.md``
    created after startup, whereas the wildcard does, with the blacklist carving
    out the entries assigned elsewhere.
    """

    @property
    def scheduler(self) -> bool:
        """Whether this Session is the workspace's fully activated scheduler.

        Used for ``list_all`` filtering and REST display; the authoritative
        ownership information lives in ``active_schedules``.
        """
        return ACTIVATE_ALL in self.active_schedules

    @property
    def ai_id(self) -> str:
        """Compatibility alias for clients that still create direct-AI sessions."""
        return self.backend_id


@dataclass
class _SessionEntry:
    scope: anyio.CancelScope
    info: SessionInfo


@dataclass
class SessionManager:
    _aim: AIManager
    _prefix: str
    _tg: Any  # anyio.TaskGroup (ty不识别的第三方类型)
    _rm: RouterManager | None = None
    _entries: dict[str, _SessionEntry] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _persist: Callable[[], Awaitable[None]] = _noop
    # Injected by Gateway.run from --default-agent / --default-workspace / --appdata.
    _default_agent: str = ""
    _default_workspace: str = ""
    _appdata: str = ""
    # 「id 以 X 开头的 Session, 其 workspace 必须显式给且落在 Y 之下」这条判据的两个参数。
    # 两者都留空 → 判据完全不存在 (见 ``_check_workspace_guard``)。**刻意是注入的参数而不是
    # 写死的 "feishu-"**: 内核不该认识产品名, 仓库已有「微内核反向依赖产品层硬编码」的账。
    # 唯一的生产填充点是 ``Gateway.run``。
    _guarded_id_prefix: str = ""
    _guarded_workspace_root: str = ""

    async def create(
        self,
        backend_type: str = "ai",
        backend_id: str = "",
        *,
        ai_id: str = "",
        id: str = "",
        workspace: str = "",
        agent: str = "",
        active_schedules: tuple[str, ...] = (),
        deactive_schedules: tuple[str, ...] = (),
        skip_workspace_guard: bool = False,
    ) -> SessionInfo:
        """Spawn a Session.

        Step 2 wiring: *agent* / *workspace* fall back to Gateway defaults when
        omitted. ``Session(agent=…)`` (from #472) then loads the capability pack
        from that directory. Tools that resolve relative paths via ContextVar
        are a later PR — this only passes the path in.

        *active_schedules* / *deactive_schedules* name, per entry, which schedules
        this Session fires (``("*",)`` = all; empty by default = none, with the
        blacklist subtracting first). The fully activated Session is created by
        ``SchedulerManager``, deduplicated per workspace and hidden from SPA /
        state. Ordinary callers pass neither argument.

        *skip_workspace_guard* 只给 **state 恢复**用 (``Gateway.run``): 恢复是把已经存在的
        东西重新拉起来, 不是创建。生产上有 14 个会话的 workspace 就是根目录, 拿判据去挡它们
        等于把这些人**迁移**掉 —— 详见 ``_check_workspace_guard``。
        """
        session_id = id or _new_uuid()
        # 判据必须在下一行那个 ``or`` 兜底**之前** —— 兜底一旦生效, 「调用方到底给没给
        # workspace」这个信息就永久丢了, 那正是 14 个会话落进公共区时发生的事。
        if not skip_workspace_guard:
            self._check_workspace_guard(session_id, workspace)
        workspace = workspace.strip() or self._default_workspace or os.getcwd()
        # Intentional: GET /defaults only announces the path; mkdir here at
        # Session create / start-chat so Haitun open does not leave an empty
        # Desktop folder.
        workspace = await ensure_workspace_dir(workspace)
        agent = agent.strip() or self._default_agent
        backend_id = backend_id or ai_id
        upstream_socket = self.resolve_backend_socket(backend_type, backend_id)
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for create {session_id!r}")
            if session_id in self._entries:
                raise ValueError(f"Session {session_id!r} already exists")
            channel_socket = _socket_path(self._prefix, "channels", session_id)
            await _ensure_socket_dir(channel_socket)
            # Hand paths to Session (#472 / #4C). Empty agent → Session uses workspace.
            sess = Session(
                workspace=workspace,
                agent=agent,
                appdata=self._appdata,
                channel_socket=channel_socket,
                ai_socket=upstream_socket,
                session_id=session_id,
                active_schedules=",".join(active_schedules),
                deactive_schedules=",".join(deactive_schedules),
            )
            scope = anyio.CancelScope()

            async def _run_session() -> None:
                try:
                    with scope:
                        await sess.run()
                except Exception as e:
                    logger.error(f"Session {session_id!r} crashed: {e!r}")
                    async with self._lock:
                        self._entries.pop(session_id, None)
                    await self._persist()

            logger.debug(f"SessionManager: starting session {session_id!r} task")
            self._tg.start_soon(_run_session)
            info = SessionInfo(
                id=session_id,
                backend_type=backend_type,
                backend_id=backend_id,
                workspace=workspace,
                channel_socket=channel_socket,
                agent=agent,
                active_schedules=active_schedules,
                deactive_schedules=deactive_schedules,
            )
            self._entries[session_id] = _SessionEntry(scope=scope, info=info)
        try:
            await _wait_socket(info.channel_socket)
        except Exception:
            logger.warning(f"Session {session_id!r} did not become ready, rolling back")
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(session_id, None)
                    scope.cancel()
                    await _remove_socket(info.channel_socket)
                await self._persist()
            raise
        await self._persist()
        logger.info(
            f"Session {session_id!r} created on {info.channel_socket} "
            f"-> {backend_type} {backend_id!r} agent={agent!r} workspace={workspace!r}"
        )
        return info

    def _check_workspace_guard(self, session_id: str, workspace: str) -> None:
        """受管前缀的 Session: workspace 必须显式给, 且严格落在受管 root 之下, 否则拒绝创建。

        治的是 15 个飞书会话把 workspace 指到 ``/workspace`` 根目录、14 个人的 agent 产出全
        写进全公司可见公共区(根目录已散着约 290 个混放文件)这件事的**初始成因**。成因那条路径
        本身仍未定 —— ``FeishuManager.route`` 吃不到下面那个兜底(它的 ws 永远非空), 所以另有
        一条拿 ``feishu-ou_*`` 形状 id 建 session 却不给 workspace 的路径。**不去猜是谁写的**:
        判据放在这里, 所有建 session 的路径都必经此处, 于是不管那条路径是谁都过不去。

        为什么是「拒绝」而不是「兜底到派生值」: 这里算不出派生值 —— 内核不知道 ``ou_*`` /
        ``chat-*`` / ``.private/*`` 三条规则(那是 ``FeishuManager.workspace_for`` 的事), 顺手
        编一个只会造出第四种目录形状。而报错会当场指出是哪个调用点漏了参数, 这正是成因未定时
        最需要的信息。

        两个字段任一为空 → 判据不存在, 直接返回。「默认关」是判据的**缺席**而非一个配置值。
        """
        prefix = self._guarded_id_prefix
        root = self._guarded_workspace_root
        if not prefix or not root or not session_id.startswith(prefix):
            return
        given = workspace.strip()
        if not given:
            raise ValueError(
                f"Session '{session_id}' needs an explicit workspace under '{root}': "
                "refusing to fall back to the default workspace, which would put this "
                "session's output in the shared root directory"
            )
        if not is_strictly_under(given, root):
            raise ValueError(
                f"Session '{session_id}' workspace '{given}' is not strictly under '{root}': "
                "refusing to create it there"
            )

    def resolve_backend_socket(self, backend_type: str, backend_id: str) -> str:
        if backend_type == "ai":
            return self._aim.get_socket(backend_id)
        if backend_type == "router":
            if self._rm is None:
                raise LookupError("Router manager is not configured")
            return self._rm.get_socket(backend_id)
        raise ValueError("backend_type must be either 'ai' or 'router'")

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            logger.debug(f"SessionManager: acquired lock for delete {session_id!r}")
            if session_id not in self._entries:
                raise LookupError(f"Session {session_id!r} not found")
            entry = self._entries.pop(session_id)
            entry.scope.cancel()
            await _remove_socket(entry.info.channel_socket)
        await self._persist()
        logger.info(f"Session {session_id!r} deleted")

    async def list_all(self, *, include_scheduler: bool = False) -> list[SessionInfo]:
        """List the user sessions.

        Scheduler Sessions are **not** included by default (刻意为之: they are not
        user sessions, and listing them in the SPA only invites deletion). Pass
        ``include_scheduler=True`` for operational or internal dedup use.
        """
        infos = [e.info for e in list(self._entries.values())]
        if include_scheduler:
            return infos
        return [info for info in infos if not info.scheduler]

    def get_socket(self, session_id: str) -> str:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].info.channel_socket

    def has(self, session_id: str) -> bool:
        return session_id in self._entries

    def get_info(self, session_id: str) -> SessionInfo:
        if session_id not in self._entries:
            raise LookupError(f"Session {session_id!r} not found")
        return self._entries[session_id].info

    def get_workspace(self, session_id: str) -> str:
        return self.get_info(session_id).workspace

    def get_agent(self, session_id: str) -> str:
        return self.get_info(session_id).agent

    def get_backend_id(self, session_id: str) -> str:
        """Backend id the session is attached to — needed when a scheduler Session reuses the same AI instance."""
        return self.get_info(session_id).backend_id
