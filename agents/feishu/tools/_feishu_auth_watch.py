"""后台收授权码 —— 把「等回调」从 Session 的 turn 锁里搬出去。

授权码回流本身是要等的: 用户点开授权页、可能先登录、再点「同意授权」, 这段时间没人能
缩短。真正的问题是**谁在等**。一旦让工具自己等 —— 在一次工具调用里轮询取件箱几分钟 ——
就等于让 SessionAgent 的 turn 等: 工具调用发生在 turn 内, turn 持有 ``anyio.Lock``,
于是用户在这几分钟里说的每句话都排在后面, 表现出来就是「机器人卡死了」。

这里换一个等法: 等待交给一个**脱离本轮**的后台任务, 工具本身立刻返回。任务用
``asyncio.create_task`` 起 (不是 anyio task group): task group 的 cancel scope 会随
本轮结束而收束, 那就等于没搬走; 裸 asyncio 任务挂在事件循环上, turn 结束照样活着。
ContextVar 在建任务那一刻被复制, 所以后台任务解析出的 workspace / session 与发起它的
那一轮一致 —— 授权 token 落在同一个 ``.psi/feishu/uat.json``。

同一个 ``user_key`` 只留一个在跑 (第二次调用返回既有状态): 取件箱是「取走即删」, 两个
watcher 会互相抢码, 抢输的那个白等到超时。上限 ``_WATCH_MAX_SECONDS`` 与 Gateway 取件箱
的 TTL 对齐 —— 码都过期了再守着没有意义。

拿到结果后由 ``notify`` 回告用户 (通常是一条私聊): 后台任务不在对话轮次里, 不回告就等于
授权悄悄成功了而用户不知道。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from psi_agent.session.task_registry import TaskRegistry

# 与 Gateway 取件箱 (``psi_agent.gateway._oauth_manager._TTL_SECONDS``) 对齐: 码在那边
# 只留 600 秒, 之后再等也取不到东西。
_WATCH_MAX_SECONDS = 600.0
_WATCH_MIN_SECONDS = 10.0

STATUS_WATCHING = "watching"
STATUS_GRANTED = "granted"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"


@dataclass
class WatchState:
    """一个 ``user_key`` 的后台收码状态。``result`` 是收码那一步的原始返回。"""

    user_key: str
    started_at: float
    timeout_seconds: float
    status: str = STATUS_WATCHING
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def remaining(self) -> float:
        return max(0.0, self.timeout_seconds - self.elapsed)

    def snapshot(self) -> dict[str, Any]:
        """给工具层看的状态摘要 (不含 task 本身, 好序列化)。"""
        return {
            "status": self.status,
            "elapsed_seconds": int(self.elapsed),
            "remaining_seconds": int(self.remaining),
            "watching": self.status == STATUS_WATCHING,
            "message": self.message,
        }


# 在跑的 (以及跑完还留着结果的) watcher 都在这张表里, 自指取消的护栏由它自带。
#
# 护栏要防的是一条**自指**路径: 收码成功后的续跑回合就跑在 watcher 自己的 task 里, 而那一轮
# 完全可以再发起一次授权 (scope 不够/换能力集), 那条路会 ``forget`` 同一个 user_key —— 于是
# 「取消自己」, 外层 anyio task group 退出时无限重试交付取消, 事件循环 100% 忙转 (2026-09-16
# 生产上锁死一个用户 3 小时)。判据、为什么 ``current_task()`` 判不出来、以及为什么不能在这里
# 再写一遍, 全在 :mod:`psi_agent.session.task_registry`。
#
# ``retain_finished_payload=True``: 这里的载荷 ``WatchState`` **就是结果** —— 后续回合靠
# ``status()`` 答「上次授权成没成」(见 ``_feishu/auth.py`` 里 STATUS_GRANTED 那几支), 所以
# 任务跑完记录不能自动摘掉, 由新一轮授权的 ``forget`` 来清。
_watchers: TaskRegistry[WatchState] = TaskRegistry("feishu-auth-watch", retain_finished_payload=True)


def clamp_timeout(timeout_seconds: float) -> float:
    return float(max(_WATCH_MIN_SECONDS, min(float(timeout_seconds), _WATCH_MAX_SECONDS)))


def status(user_key: str) -> WatchState | None:
    """该 ``user_key`` 的收码状态; 从未收过返回 None。"""
    return _watchers.get(user_key)


def is_watching(user_key: str) -> bool:
    state = _watchers.get(user_key)
    return state is not None and state.status == STATUS_WATCHING


def forget(user_key: str) -> asyncio.Task[None] | None:
    """丢掉记录 (重新发起授权时调用, 免得旧结果被当成本次的)。

    返回被取消的 task, 供调用方 ``await`` —— ``cancel()`` 只是**提出**取消, 任务真正收尾
    (以及它持有的资源被释放) 要等事件循环再调度它。需要资源确实腾出来的场景请用
    :func:`forget_and_wait`。

    **绝不取消自己所在的那个 watcher**: 记录照样丢 (新一轮授权不能读到旧结果), 但不动那个
    task —— 这一支由 :class:`~psi_agent.session.task_registry.TaskRegistry` 自己判, 见上面注释。
    """
    return _watchers.forget(user_key)


async def forget_and_wait(user_key: str, *, seconds: float = 2.0) -> None:
    """撤掉 watcher 并**等它真的收尾**。

    loopback 模式下这是硬要求: watcher 占着回环端口, 而 ``plan_receiver`` 用「端口空不空」
    判断还能不能自动收码。没等它关掉就去重新规划通道, 免复制的授权会被静默降级成手工贴码。

    刻意**不** ``suppress(asyncio.CancelledError)`` (原实现有): 那个 suppress 把「本任务的取消
    已提出」从调用栈里抹掉, 是活锁的必要条件。本任务真被取消时异常必须继续向外传播。
    """
    await _watchers.forget_and_wait(user_key, seconds=seconds)


def reset_all() -> None:
    """测试用: 清空全部 watcher。"""
    _watchers.reset_all()


async def _run(
    state: WatchState,
    collect: Callable[[str, float], Awaitable[dict[str, Any]]],
    notify: Callable[[str, WatchState], Awaitable[None]] | None,
) -> None:
    """守到码回来 (或超时), 记下结果并回告用户。

    这里刻意把所有异常都吞掉并记进 ``state``: 后台任务没有调用方接它的错, 抛出去只会变成
    事件循环里一条 "Task exception was never retrieved", 用户那边则永远等不到回话。

    「这一整棵执行流 (含 ``notify`` 里的续跑回合和它跑的工具) 算作自己」是由外面那层
    ``TaskRegistry.run_registered`` 立的 ContextVar 标出来的 —— 标必须在**任务体内**立,
    在建任务前立会把发起方那条执行流一起标上, 于是发起方后续撤这个 key 也被当成自指。
    """
    try:
        result = await collect(state.user_key, state.timeout_seconds)
    except asyncio.CancelledError:
        state.status = STATUS_FAILED
        state.message = "后台收码被取消"
        raise
    except Exception as exc:  # 详见上面注释: 后台任务无人接错, 抛出去只会变成一条无人理的日志
        logger.error(f"Feishu auth watcher failed for {state.user_key!r}: {exc!r}")
        state.status = STATUS_FAILED
        state.message = f"后台收码出错: {exc}"
        state.result = {"ok": False, "message": state.message}
    else:
        state.result = result
        if result.get("ok"):
            state.status = STATUS_GRANTED
            state.message = str(result.get("message") or "授权已完成")
        else:
            state.status = STATUS_TIMEOUT if result.get("timed_out") else STATUS_FAILED
            state.message = str(result.get("message") or "授权未完成")
    if notify is not None:
        try:
            await notify(state.user_key, state)
        except Exception as exc:  # 回告失败不该盖掉收码结果
            logger.warning(f"Feishu auth watcher notify failed for {state.user_key!r}: {exc!r}")


def start(
    user_key: str,
    collect: Callable[[str, float], Awaitable[dict[str, Any]]],
    *,
    notify: Callable[[str, WatchState], Awaitable[None]] | None = None,
    timeout_seconds: float = _WATCH_MAX_SECONDS,
) -> tuple[WatchState, bool]:
    """起一个后台收码任务; 返回 ``(状态, 是否新起的)``。

    已有在跑的直接返回既有状态 (``False``) —— 见模块文档: 两个 watcher 会抢码。
    起不了后台任务时抛 ``RuntimeError``, 由调用方决定退路 (别假装已经在收了)。
    """
    existing = _watchers.get(user_key)
    if existing is not None and existing.status == STATUS_WATCHING:
        return existing, False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError("没有运行中的 asyncio 事件循环, 无法把收码放到后台") from exc
    state = WatchState(
        user_key=user_key,
        started_at=time.monotonic(),
        timeout_seconds=clamp_timeout(timeout_seconds),
    )
    # 任务体包在 ``run_registered`` 里: 它在任务体第一行立起「当前执行流属于这个 key」的标记,
    # 自指取消的护栏靠这个标记才判得出来。
    #
    # ``create_task`` 只是排程 (到下一个 await 点才真跑), 所以先建任务再 ``register`` 是安全的;
    # 反过来 ``register`` 需要 task 对象, 只能是这个顺序。两处都必须留强引用 (表里 + state.task):
    # 只被局部变量持有的任务会被 GC 掉, 事件循环随后把它当「任务被销毁但仍在 pending」处理,
    # 码就再也没人取了。
    state.task = loop.create_task(_watchers.run_registered(user_key, _run(state, collect, notify)))
    _watchers.register(user_key, state.task, payload=state)
    return state, True
