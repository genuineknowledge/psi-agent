"""受管后台任务表 —— 取消原语自带自指检查。

工具起的后台任务 (收授权码、守一个外部回调) 要脱离本轮活着, 所以用裸 ``asyncio.Task``
而不是 anyio task group: task group 的 cancel scope 随本轮收束, 那就等于没搬走。代价是
这些任务要自己管生命周期, 而「重新发起同一件事时先撤掉旧的那个」这一步藏着一条自指路径:

    后台任务拿到结果 → 回告/续跑一个回合 → 那一轮再发起同一件事 → 撤掉同 key 的任务
                                                                  ↑ 就是当前这条执行流

于是 ``task.cancel()`` 取消的是自己所在的任务。若这次取消又被 ``suppress`` 吞掉, 任务便
停在「取消已提出却仍活着」的状态; 外层 anyio task group 的 ``__aexit__`` 会靠
``loop.call_soon`` 无限重试交付取消, 目标任务永远不进入 cancelled —— 事件循环 100% 忙转,
持有的锁永不释放。2026-09-16 生产上一个用户 session 因此锁死约 3 小时, 且不会自行恢复。

本模块把这条护栏放在**原语**里而不是每个调用点各写一遍: 调用点凭直觉写的检查基本都是
无效的 (见 :meth:`TaskRegistry.forget` 里为什么 ``current_task()`` 不行)。

值只有 key → task 是不够的: 调用方通常要在表里挂自己的状态对象 (飞书授权那份挂
``WatchState``), 所以载荷用类型参数 ``T`` 放开, 注册表不认识它的形状。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from loguru import logger

# 当前执行流正跑在哪个受管任务里 (空串 = 不在任何受管任务里)。
#
# 为什么不用 ``asyncio.current_task()``: 后台任务续跑的那一轮里, 工具是在
# ``anyio.create_task_group()`` 的**子任务**里执行的, 当前任务因此是那个子任务, 而不是被
# 登记的任务本身 —— 按 ``current_task()`` 写的守卫在真实形状下判不出自指 (实测仍然活锁)。
# ContextVar 在建任务那一刻被复制, 所以整棵子任务树都读得到祖先立的值, 判得出来。
#
# 每个注册表一个 ContextVar 会让「跑在 A 表的任务里」污染不到 B 表, 但也就多了一份隐式
# 状态; 这里用模块级单例 + ``(registry_name, key)`` 组合值, 同一个进程里多张表互不误判。
_CURRENT_TASK_KEY: contextvars.ContextVar[str] = contextvars.ContextVar("psi_managed_task_key", default="")


@dataclass
class _Entry[T]:
    task: asyncio.Task[None]
    payload: T | None


class TaskRegistry[T]:
    """一组 ``key -> asyncio.Task`` (可挂调用方自定的载荷), 取消操作自带自指检查。

    ``name`` 只用于区分同进程里的多张表与写日志; 它进 ContextVar 的值, 所以两张表登记了
    同一个 ``key`` 也不会互相认成自己。
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._entries: dict[str, _Entry[T]] = {}

    # ── 登记与查询 ────────────────────────────────────────────────────────────

    def register(self, key: str, task: asyncio.Task[None], payload: T | None = None) -> None:
        """把 ``task`` 登记在 ``key`` 下。

        调用方必须自己持有强引用或依赖本表: 只被局部变量持有的任务会被 GC 掉, 事件循环随后
        把它当「任务被销毁但仍在 pending」处理, 那件活就再也没人干了。
        """
        self._entries[key] = _Entry(task=task, payload=payload)

    def get(self, key: str) -> T | None:
        """``key`` 的载荷; 没登记过 (或已摘掉) 返回 None。"""
        entry = self._entries.get(key)
        return None if entry is None else entry.payload

    def task_for(self, key: str) -> asyncio.Task[None] | None:
        """``key`` 的 task; 没登记过 (或已摘掉) 返回 None。"""
        entry = self._entries.get(key)
        return None if entry is None else entry.task

    def keys(self) -> list[str]:
        return list(self._entries)

    # ── 任务体包装 ────────────────────────────────────────────────────────────

    async def run_registered(self, key: str, coro: Coroutine[Any, Any, None]) -> None:
        """受管任务的任务体: 第一行立 ContextVar, 结束时摘掉自己的记录。

        ``set`` 必须在**任务体内**而不是建任务前: 在外面 set 会连发起方那条执行流一起标上,
        于是发起方后续撤这个 key 也被当成自指而不取消。

        结束时只摘「还是自己那条」记录: 期间可能已有人 ``forget`` 过并重新 ``register`` 了一个
        新任务, 无条件 ``pop`` 会把新的那个从表里抹掉, 表现成「明明在跑却查不到」。
        """
        _CURRENT_TASK_KEY.set(self._scoped(key))
        try:
            await coro
        finally:
            entry = self._entries.get(key)
            if entry is not None and entry.task is asyncio.current_task():
                del self._entries[key]

    # ── 取消 ──────────────────────────────────────────────────────────────────

    def forget(self, key: str) -> asyncio.Task[None] | None:
        """摘掉 ``key`` 的记录并取消它的 task; 返回被取消的 task 供调用方 ``await``。

        ``cancel()`` 只是**提出**取消, 任务真正收尾 (以及它占的资源被释放) 要等事件循环再
        调度它。需要资源确实腾出来的场景用 :meth:`forget_and_wait`。

        没有可取消的 task 时返回 None —— 包括「当前执行流就在这个 key 的任务里」这一支:
        记录照样摘 (新一轮不能读到旧载荷), 但**绝不取消自己**, 原因见模块文档。
        """
        entry = self._entries.pop(key, None)
        if entry is None or entry.task.done():
            return None
        if _CURRENT_TASK_KEY.get() == self._scoped(key):
            # 自指: 当前执行流就在这个 key 的任务里, 或它派生的某个子任务里。
            #
            # 这里**不能**换成 ``entry.task is asyncio.current_task()``: 工具跑在
            # ``anyio.create_task_group()`` 的子任务里, 当前任务是那个子任务而不是
            # ``entry.task``, 于是判等恒为假、守卫恒不生效 (按这个思路写的第一版修复实测
            # 仍然活锁)。ContextVar 随子任务继承, 这是它判得出来的唯一原因 —— 别简化回去。
            logger.info(
                f"TaskRegistry({self._name}): not cancelling the task we are running inside "
                f"for {key!r}; dropped the record only"
            )
            return None
        entry.task.cancel()
        return entry.task

    async def forget_and_wait(self, key: str, *, seconds: float = 2.0) -> None:
        """撤掉 ``key`` 并**等它真的收尾** (最多 ``seconds`` 秒)。

        刻意**不** ``suppress(asyncio.CancelledError)``: 那个 suppress 是活锁的必要条件 ——
        它把「本任务的取消已经提出」这个事实从调用栈里抹掉, 任务于是吸收了一次取消却继续跑,
        外层 task group 便无限重试交付。本任务真被取消时, ``CancelledError`` 必须继续向外传播。

        等法用 ``asyncio.wait`` 而不是裸 ``await task``: 后者会把**目标**任务的
        ``CancelledError`` 抛进调用方 (原实现正因此才 suppress 它)。``asyncio.wait`` 不搬运
        目标任务的结果, 所以「目标被取消」与「本任务被取消」这两件事不再混为一谈。
        """
        task = self.forget(key)
        if task is None:
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await asyncio.wait({task})

    def reset_all(self) -> None:
        """摘掉并取消全部记录 (自指的那个只摘不取消)。"""
        for key in list(self._entries):
            self.forget(key)

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _scoped(self, key: str) -> str:
        """ContextVar 里存的值: 带表名, 免得两张表登记同名 key 时互相认成自己。"""
        return f"{self._name}\x00{key}"
