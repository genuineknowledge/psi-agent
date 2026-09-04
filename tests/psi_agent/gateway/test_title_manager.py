from __future__ import annotations

import anyio
import pytest

from psi_agent.runtime._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "测试标题")
    assert m.get_all() == {"s1": "测试标题"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_title_manager_concurrency() -> None:
    persisted: list[dict[str, str]] = []

    async def mock_persist() -> None:
        await anyio.sleep(0.001)
        persisted.append(m.get_all())

    m = TitleManager(_persist=mock_persist)

    async def worker(sid: str, title: str) -> None:
        await m.set(sid, title)

    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(worker, f"s{i}", f"Title {i}")

    assert len(m.get_all()) == 10
    assert len(persisted) == 10
