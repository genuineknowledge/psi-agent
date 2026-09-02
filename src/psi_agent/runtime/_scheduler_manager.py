"""SchedulerManager — 每个 workspace 恰好一个全量激活的调度 Session。

**为什么 (刻意为之)**

定时任务归属 **workspace**, 触发权归属 **(session x schedule)**。飞书 channel 按
open_id 给每个用户 spawn 一个独立 Session (``_feishu_manager.py``), SPA 也可能对同一
workspace 开多个会话; 每个 Session 都能读到 ``{workspace}/schedules`` 的全部条目, 但
一条 schedule 必须**恰好被一个 Session 激活**, 否则一条定时提醒会被在线会话数乘一遍。

本模块负责那个「恰好一个」: ``ensure(workspace)`` 幂等地为一个 workspace 拿到/创建
唯一的**全量激活** (``active_schedules=("*",)``) 调度 Session, 用户会话则一律不激活
任何条目。于是「重复触发」在构造期就不存在, 不需要运行时抢锁, 也没有「持有者退出后
谁接管」的选主问题。

粒度是逐条而非整个 Session 一个布尔: 布尔只能表达「全触发 / 全不触发」, 表达不了
「A 条归调度 Session、B 条归某个用户会话」。Gateway 默认用 ``("*",)`` 把整个 workspace
交给调度 Session, 但 Session 层的名单机制允许更细的划分 (见 ``session/AGENTS.md``)。

**按需创建**: 只有 workspace 真的存在非空 ``schedules/`` 时才 spawn, 免得 N 个
从不用定时任务的飞书用户各挂一个空调度 Session (每个都要付 tools 加载成本)。
调用方在建 workspace / 路由用户 / 恢复 state 后调 ``ensure``; 被跳过的 workspace
记入 ``_pending``, 由常驻 ``watch_loop`` 每 ``_WATCH_INTERVAL_SECONDS`` 重查 ——
用户新建第一个定时任务后**不需要任何外部事件** (下一次 ensure / 重启 / 新消息)
就会被自动拉起。旧行为是「到点不触发、必须唤醒」: 调度 Session 不 spawn 就没有
``_watch_dir``, 而 ``schedule_manage`` 写 TASK.md 这件事本身不会触发 ensure。

**对 SPA / state 完全隐藏**: ``SessionInfo.scheduler`` (由 ``active_schedules`` 含
``*`` 派生) 使其从 ``SessionManager.list_all()`` 与 ``state/latest.json`` 中排除。
session id 由 workspace 路径确定性派生 (``_workspace_key`` 归一后取 sha256 前 16 位),
因此重启后 ``ensure`` 会重建同名 Session, 无需持久化。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

import anyio
from loguru import logger

from psi_agent.runtime._session_manager import SessionManager
from psi_agent.session.schedule_registry import ACTIVATE_ALL

# 被 ensure 跳过 (暂无 schedules) 的 workspace 多久重查一次。与调度 Session 自己的
# ``_watch_dir`` 轮询周期一致: 用户新建定时任务后最多等这么久就被拉起。
_WATCH_INTERVAL_SECONDS = 30.0


@dataclass
class SchedulerManager:
    """按 workspace 去重地持有调度 Session。

    ``_ai_id`` 是调度 Session 挂载的缺省 AI 实例; 为空时 ``ensure`` 直接跳过
    (记 warning) —— 没有 AI 后端时 ``fire=prompt`` 无法工作, 但 spawn 一个连不上
    上游的 Session 更糟。

    ``_pending`` 是「按需 spawn 跳过但可能随时出现 schedules」的 workspace:
    ``ensure`` 因暂无 schedules 而跳过时记入, ``watch_loop`` 每 30s 重查, 一旦
    ``schedules/*/TASK.md`` 出现就按记下的 ai/agent 拉起调度 Session 并出队 ——
    首个定时任务因此不再依赖下一次 ``ensure`` 碰巧发生。
    """

    _sm: SessionManager
    _ai_id: str = ""
    _routes: dict[str, str] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    # workspace_key -> (workspace, ai_id, agent); 仅记「有 AI 可用但暂无 schedules」的。
    _pending: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # 公司级种子任务的落点与来源: agent 包内置的 ``schedules/*/TASK.md`` 幂等 seed 进
    # *seed_workspace* (部署时经 ``PSI_SEED_SCHEDULES_WORKSPACE`` 指定, 空 = 关闭)。
    # 只 seed 这一个 workspace: 飞书每用户一个 workspace, 全 seed 会让每个在线用户的
    # 调度 Session 各跑一遍「提醒全表所有人」, 消息按在线人数翻倍。
    seed_workspace: str = ""
    seed_agent: str = ""

    @staticmethod
    async def _workspace_key(workspace: str) -> str:
        """规范化 workspace 路径 - 大小写 / 斜杠差异不该产出两个调度 Session。

        用 ``anyio.Path.resolve()`` 而非 ``os.path.realpath`` —— 后者在 async
        上下文里是同步 IO (会 stat 磁盘), 违反「一切异步」约定。``normcase``
        是纯字符串运算, 无 IO, 可直接用。
        """
        resolved = str(await anyio.Path(workspace).resolve())
        return os.path.normcase(resolved)

    @staticmethod
    def _session_id_from_key(key: str) -> str:
        """由**已规范化**的 workspace key 派生确定性 session id。

        加 ``scheduler-`` 前缀与用户会话 / 飞书会话的命名空间隔离; 用 hash 而非
        路径本身: 路径含分隔符 / 中文 / 超长, 不适合做 socket 文件名。
        """
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return f"scheduler-{digest}"

    async def ensure(self, workspace: str, *, ai_id: str = "", agent: str = "") -> str:
        """确保 *workspace* 有且仅有一个调度 Session; 返回其 session id (跳过时 ``""``)。

        幂等: 已存在则直接返回。``schedules/`` 不存在或为空时**不** spawn (按需),
        但有可用 AI 时把 workspace 记入 ``_pending``, 由 ``watch_loop`` 稍后自动拉起
        —— 首个定时任务无需任何外部事件即可生效。任何异常都只记 warning 并返回
        ``""`` —— 调度起不来不该拖垮建会话 / 收消息的主链路。
        """
        if not workspace.strip():
            return ""
        try:
            return await self._do_ensure(workspace, ai_id=ai_id, agent=agent)
        except Exception as e:
            logger.warning(f"SchedulerManager: failed to ensure scheduler for {workspace!r}: {e!r}")
            return ""

    async def _seed_missing_schedules(self, workspace: str, agent: str) -> bool:
        """把 agent 包内置的种子任务幂等落进 seed workspace 的 ``schedules/``。

        只补 workspace 里**没有同名目录**的任务, 已存在的一律不覆盖 —— 用户改过的
        口径、删掉的任务都不许被 seed 回来。仅当 *workspace* 恰好是配置的
        ``seed_workspace`` 时执行 (公司级任务只跑一份, 见字段注释); 返回是否落入了
        至少一个新任务。
        """
        if not self.seed_workspace.strip():
            return False
        # agent 包来源优先取构造时的 seed_agent; ensure 传的 agent 是会话自己的包路径,
        # 常常为空或指向别的包, 不能拿它当种子来源。
        agent = agent or self.seed_agent
        if not agent.strip():
            return False
        if await self._workspace_key(workspace) != await self._workspace_key(self.seed_workspace):
            return False
        src_dir = anyio.Path(agent) / "schedules"
        if not await src_dir.is_dir():
            return False
        dst_dir = anyio.Path(workspace) / "schedules"
        seeded = False
        async for task_dir in src_dir.iterdir():
            if not await task_dir.is_dir():
                continue
            src_task = task_dir / "TASK.md"
            if not await src_task.exists():
                continue
            dst_task = dst_dir / task_dir.name / "TASK.md"
            if await dst_task.exists():
                continue
            await (dst_dir / task_dir.name).mkdir(parents=True, exist_ok=True)
            await dst_task.write_text(await src_task.read_text(encoding="utf-8"), encoding="utf-8")
            seeded = True
            logger.info(f"SchedulerManager: seeded schedule {task_dir.name!r} from agent package into {workspace!r}")
        return seeded

    async def _do_ensure(self, workspace: str, *, ai_id: str, agent: str) -> str:
        key = await self._workspace_key(workspace)
        sid = self._session_id_from_key(key)
        async with self._lock:
            logger.debug(f"SchedulerManager: acquired lock for ensure {workspace!r}")
            # 公司级种子任务随 agent 包部署: 每次 ensure 都幂等补一遍 (已 spawn 的
            # Session 也受益 —— 它自己的 _watch_dir 会在 30s 内拾取新落盘的任务)。
            try:
                await self._seed_missing_schedules(workspace, agent)
            except Exception as e:
                logger.warning(f"SchedulerManager: failed to seed schedules into {workspace!r}: {e!r}")
            cached = self._routes.get(key)
            if cached is not None and self._sm.has(cached):
                return cached
            # 路由表未命中但 Session 已在 (重启后 ensure 重建同名, 或并发抢先) → adopt。
            if self._sm.has(sid):
                self._routes[key] = sid
                logger.debug(f"SchedulerManager: adopted existing scheduler session {sid!r}")
                return sid

            resolved_ai = ai_id or self._ai_id
            if not await self._has_schedules(workspace):
                if resolved_ai:
                    # watch_loop 每 _WATCH_INTERVAL_SECONDS 重查本条目, schedules/ 一
                    # 出现就自动拉起 —— 首个定时任务不再等下一次 ensure 碰巧发生。
                    self._pending[key] = (workspace, resolved_ai, agent)
                    logger.debug(f"SchedulerManager: {workspace!r} has no schedules yet; queued for watch_loop")
                else:
                    logger.debug(f"SchedulerManager: no schedules under {workspace!r}; not spawning")
                return ""

            if not resolved_ai:
                logger.warning(
                    f"SchedulerManager: {workspace!r} has schedules but no ai_id is configured; "
                    "scheduler session not started"
                )
                return ""

            try:
                await self._sm.create(
                    ai_id=resolved_ai,
                    id=sid,
                    workspace=workspace,
                    agent=agent,
                    active_schedules=(ACTIVATE_ALL,),
                )
            except ValueError as e:
                # 并发竞态: 另一路已建同名 (锁内理论不会, 防御性兜底)。
                if "already exists" not in str(e):
                    raise
                logger.debug(f"SchedulerManager: scheduler session {sid!r} raced")

            self._routes[key] = sid
            logger.info(f"SchedulerManager: scheduler session {sid!r} owns schedules of {workspace!r}")
            return sid

    async def watch_loop(self) -> None:
        """常驻协程: 每 ``_WATCH_INTERVAL_SECONDS`` 重查 ``_pending`` 里的 workspace。

        刻意为之: ``ensure`` 只在「schedules 已存在」时 spawn, 而它的调用时机
        (建会话 / 路由 / 恢复 state) 全都不是「``schedule_manage`` 写入第一个
        TASK.md」这件事本身 —— 没有这个循环, 首个定时任务会一直等到下一次
        ``ensure`` 碰巧发生 (飞书还得等 channel 路由缓存失效), 到点不触发、
        看起来必须「唤醒」。调度 Session 一旦拉起, 其自身的 ``_watch_dir`` 接管
        后续增删改, 本循环随即出队该 workspace。

        ``Gateway.run`` 在启动时 ``start_soon`` 本协程; 任何一轮失败只记 warning
        下轮重试, ``CancelledError`` 是 ``BaseException`` 照常传播 (随 Gateway
        关闭而终止)。
        """
        logger.info(f"SchedulerManager: watch_loop started (every {_WATCH_INTERVAL_SECONDS}s)")
        try:
            while True:
                await anyio.sleep(_WATCH_INTERVAL_SECONDS)
                try:
                    await self._sweep_once()
                except Exception as e:
                    logger.warning(f"SchedulerManager: watch_loop iteration failed: {e!r}")
        finally:
            logger.info("SchedulerManager: watch_loop stopped")

    async def _sweep_once(self) -> None:
        """一轮 pending 重查: 有 schedules 的 workspace 立即拉起调度 Session。

        同时兜底 seed workspace 的冷启动 —— 它可能从没被任何用户消息 ensure 过
        (不在 ``_pending`` 里), 不在这里主动 ensure 的话, 部署后种子任务永远不落盘。
        """
        if self.seed_workspace.strip():
            seed_sid = self._session_id_from_key(await self._workspace_key(self.seed_workspace))
            if not self._sm.has(seed_sid):
                await self.ensure(self.seed_workspace, ai_id=self._ai_id, agent=self.seed_agent)
        for key, (workspace, ai_id, agent) in list(self._pending.items()):
            sid = self._session_id_from_key(key)
            if self._sm.has(sid):
                # 已被并发 ensure / 本循环拉起的路径建好, 无需再等。
                self._pending.pop(key, None)
                continue
            if not await self._has_schedules(workspace):
                continue
            spawned = await self.ensure(workspace, ai_id=ai_id, agent=agent)
            if spawned == sid:
                self._pending.pop(key, None)
                logger.info(f"SchedulerManager: watch_loop spawned scheduler {sid!r} for {workspace!r}")

    @staticmethod
    async def _has_schedules(workspace: str) -> bool:
        """workspace 下是否有至少一个 ``schedules/*/TASK.md`` (按需 spawn 的判据)。"""
        sched_dir = anyio.Path(workspace) / "schedules"
        if not await sched_dir.is_dir():
            return False
        async for task_dir in sched_dir.iterdir():
            if await task_dir.is_dir() and await (task_dir / "TASK.md").exists():
                return True
        return False
