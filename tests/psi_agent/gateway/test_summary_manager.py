from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._summary_manager import SummaryManager


@pytest.mark.anyio
async def test_summary_set_get_delete() -> None:
    m = SummaryManager()
    await m.set("s1", "为星辰科技写办公室剧本杀角色卡")
    assert m.get_all() == {"s1": "为星辰科技写办公室剧本杀角色卡"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_summary_manager_concurrent_lock() -> None:
    order: list[str] = []

    async def slow_persist() -> None:
        order.append("start_persist")
        await anyio.sleep(0.05)
        order.append("end_persist")

    m = SummaryManager(_persist=slow_persist)

    async with anyio.create_task_group() as tg:
        tg.start_soon(m.set, "s1", "Summary 1")
        tg.start_soon(m.set, "s2", "Summary 2")

    # Lock ensures tasks run serially through set/persist
    assert order == ["start_persist", "end_persist", "start_persist", "end_persist"]
    assert m.get_all() == {"s1": "Summary 1", "s2": "Summary 2"}
