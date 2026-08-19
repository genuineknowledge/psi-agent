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
async def test_summary_concurrent_updates() -> None:
    calls = 0

    async def fake_persist() -> None:
        nonlocal calls
        calls += 1

    m = SummaryManager(_persist=fake_persist)

    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(m.set, f"s{i}", f"summary {i}")

    assert len(m.get_all()) == 10
    assert calls == 10
