"""``TaskRegistry`` 的自指取消守卫。

这些用例钉住的是 2026-09-16 生产事故的形状: 一条执行流取消它自己所在的 asyncio 任务,
``CancelledError`` 被吞掉, 任务于是「取消已提出却仍活着」, 外层 anyio task group 的
``__aexit__`` 便靠 ``loop.call_soon`` 无限重试交付取消 —— 事件循环 100% 忙转,
``turn_lock`` 永不释放, 一个飞书用户 session 锁死 3 小时。

(c)/(d) 两条必须带 ``anyio.create_task_group()``: 活锁**只在**取消交付撞上 task group
退出等待时出现, 少了那一层就永远测不出来。
"""

from __future__ import annotations

import asyncio
from typing import Any

import anyio
import pytest
from loguru import logger

from psi_agent.session.task_registry import TaskRegistry


def _spawn[T](registry: TaskRegistry[T], key: str, coro: Any, payload: T | None = None) -> asyncio.Task[None]:
    """按生产用法起一个受管任务: ``run_registered`` 立 ContextVar, 随后登记。

    ``create_task`` 只是排程, 到下一个 await 点才跑, 所以 ``register`` 一定早于任务体。
    """
    task = asyncio.get_running_loop().create_task(registry.run_registered(key, coro))
    registry.register(key, task, payload=payload)
    return task


async def test_forget_cancels_a_task_registered_under_another_key() -> None:
    """反例边: 守卫不能退化成「永不取消」—— 跨 key 的 forget 必须真的取消。"""
    registry: TaskRegistry[None] = TaskRegistry("test-cross-key")
    started = asyncio.Event()

    async def _body() -> None:
        started.set()
        await anyio.sleep_forever()

    task = _spawn(registry, "other", _body())
    await started.wait()

    assert registry.forget("other") is task
    with anyio.fail_after(5):
        await asyncio.wait({task})
    assert task.cancelled()
    assert registry.get("other") is None


async def test_forget_inside_the_registered_task_drops_the_record_only() -> None:
    """(b) 任务体里 forget 自己的 key: 返回 None、任务不被取消, 并记一条 INFO。"""
    registry: TaskRegistry[str] = TaskRegistry("test-self")
    progress: list[str] = []
    returned: list[Any] = []
    # loguru 不走 stdlib logging 的 handler 链, ``caplog`` 收不到它的输出 —— 阴性断言会假绿。
    lines: list[tuple[str, str]] = []
    sink_id = logger.add(lambda msg: lines.append((msg.record["level"].name, msg.record["message"])), level="INFO")
    try:

        async def _body() -> None:
            progress.append("start")
            returned.append(registry.forget("self"))
            # 给取消一次交付机会: 真被取消了, 这个 await 点就抛。
            await asyncio.sleep(0)
            progress.append("end")

        task = _spawn(registry, "self", _body(), payload="state")
        with anyio.fail_after(5):
            await asyncio.wait({task})
    finally:
        logger.remove(sink_id)

    assert progress == ["start", "end"]
    assert returned == [None]
    assert not task.cancelled()
    assert task.exception() is None
    # 记录照样摘掉: 重新发起的那一轮不能读到旧载荷。
    assert registry.get("self") is None
    assert [msg for level, msg in lines if level == "INFO" and "self" in msg], lines


async def test_forget_from_a_child_task_of_a_task_group_drops_the_record_only() -> None:
    """(c) 事故的真实形状: forget 发生在任务体内 ``anyio`` task group 的**子任务**里。

    ``asyncio.current_task()`` 在这里是那个子任务, 不是被登记的任务, 所以按它写的守卫
    判不出自指 (实测「STILL LIVELOCKED」)。守卫必须用随子任务继承的 ContextVar。
    """
    registry: TaskRegistry[None] = TaskRegistry("test-self-in-task-group")
    returned: list[Any] = []

    async def _body() -> None:
        async with anyio.create_task_group() as tg:

            async def _tool() -> None:
                returned.append(registry.forget("self"))

            tg.start_soon(_tool)

    task = _spawn(registry, "self", _body())
    with anyio.fail_after(5):
        await asyncio.wait({task})

    assert returned == [None]
    assert not task.cancelled()
    assert task.exception() is None


async def test_reauthorizing_inside_a_tool_task_group_does_not_livelock() -> None:
    """(d) 与 (c) 同一条路, 但判据是「这个 await 会返回」: 活锁下它永不返回。

    走 ``forget_and_wait`` 而不是 ``forget``: 生产上那条路是它, 且它是 ``suppress`` 吞掉
    取消的地方。``fail_after`` 就是判据本身。
    """
    registry: TaskRegistry[None] = TaskRegistry("test-livelock")
    progress: list[str] = []

    async def _body() -> None:
        progress.append("turn-start")
        async with anyio.create_task_group() as tg:

            async def _auth_request_tool() -> None:
                await registry.forget_and_wait("self", seconds=2.0)

            tg.start_soon(_auth_request_tool)
        progress.append("turn-finished")

    task = _spawn(registry, "self", _body())
    with anyio.fail_after(5):
        await asyncio.wait({task})

    assert progress == ["turn-start", "turn-finished"]
    assert task.exception() is None


async def test_forget_and_wait_across_keys_does_not_raise_cancelled_into_the_caller() -> None:
    """跨 key 等收尾时, 被取消的是**目标**任务, 调用方必须照常往下走。

    这条钉住 ``forget_and_wait`` 的等法: 裸 ``await task`` 会把目标任务的 ``CancelledError``
    抛进调用方 (这正是原实现要 ``suppress(CancelledError)`` 的原因, 而那个 suppress 是活锁的
    必要条件)。所以这里用 ``asyncio.wait`` —— 它不搬运目标任务的结果, 而本任务真被取消时
    ``CancelledError`` 仍照常向外传播。
    """
    registry: TaskRegistry[None] = TaskRegistry("test-cross-key-wait")
    started = asyncio.Event()

    async def _body() -> None:
        started.set()
        await anyio.sleep_forever()

    task = _spawn(registry, "other", _body())
    await started.wait()

    with anyio.fail_after(5):
        await registry.forget_and_wait("other", seconds=2.0)

    assert task.cancelled()


async def test_reset_all_forgets_every_key() -> None:
    registry: TaskRegistry[None] = TaskRegistry("test-reset")
    started = asyncio.Event()

    async def _body() -> None:
        started.set()
        await anyio.sleep_forever()

    task = _spawn(registry, "a", _body())
    await started.wait()

    registry.reset_all()

    assert registry.get("a") is None
    with anyio.fail_after(5):
        await asyncio.wait({task})
    assert task.cancelled()


async def test_forget_is_a_noop_for_an_unknown_key() -> None:
    registry: TaskRegistry[None] = TaskRegistry("test-unknown")
    assert registry.forget("nope") is None
    with anyio.fail_after(5):
        await registry.forget_and_wait("nope", seconds=2.0)


@pytest.mark.parametrize("payload", ["state", None])
async def test_get_returns_the_caller_payload_until_the_task_body_finishes(payload: str | None) -> None:
    """载荷是调用方自己的对象 (飞书那份是 ``WatchState``), 注册表不写死只存 task。"""
    registry: TaskRegistry[str] = TaskRegistry("test-payload")
    release = asyncio.Event()

    async def _body() -> None:
        await release.wait()

    task = _spawn(registry, "k", _body(), payload=payload)
    await asyncio.sleep(0)
    assert registry.get("k") == payload

    release.set()
    with anyio.fail_after(5):
        await asyncio.wait({task})
    # 任务体跑完后自己摘记录, 免得表里堆已死的 key。
    assert registry.get("k") is None
