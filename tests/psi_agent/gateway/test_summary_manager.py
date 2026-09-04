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
async def test_summary_manager_concurrency() -> None:
    persisted: list[dict[str, str]] = []

    async def mock_persist() -> None:
        await anyio.sleep(0.001)
        persisted.append(m.get_all())

    m = SummaryManager(_persist=mock_persist)

    async def worker(sid: str, summary: str) -> None:
        await m.set(sid, summary)

    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(worker, f"s{i}", f"Summary {i}")

    assert len(m.get_all()) == 10
    assert len(persisted) == 10
