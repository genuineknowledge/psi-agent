from __future__ import annotations

import anyio
import pytest

from psi_agent.runtime._summary_manager import SummaryManager


@pytest.mark.anyio
async def test_summary_set_get_delete() -> None:
    m = SummaryManager()
    await m.set("s1", "为星辰科技写办公室剧本杀角色卡")
    assert m.get_all() == {"s1": "为星辰科技写办公室剧本杀角色卡"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_summary_manager_concurrent_set() -> None:
    calls = []

    async def mock_persist() -> None:
        calls.append("persist")
        await anyio.sleep(0.01)

    m = SummaryManager(_persist=mock_persist)

    async with anyio.create_task_group() as tg:
        tg.start_soon(m.set, "s1", "Summary 1")
        tg.start_soon(m.set, "s2", "Summary 2")

    assert len(m.get_all()) == 2
    assert len(calls) == 2
