from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_manager_crud() -> None:
    persisted = False

    async def fake_persist() -> None:
        nonlocal persisted
        persisted = True

    m = TitleManager(_persist=fake_persist)
    assert m.get_all() == {}

    await m.set("s1", "Test Title")
    assert m.get_all() == {"s1": "Test Title"}
    assert persisted is True

    persisted = False
    await m.delete("s1")
    assert m.get_all() == {}
    assert persisted is True

    # Delete non-existent session is idempotent
    persisted = False
    await m.delete("s1")
    assert persisted is False


@pytest.mark.anyio
async def test_title_manager_concurrent_lock() -> None:
    order: list[str] = []

    async def slow_persist() -> None:
        order.append("start_persist")
        await anyio.sleep(0.05)
        order.append("end_persist")

    m = TitleManager(_persist=slow_persist)

    async with anyio.create_task_group() as tg:
        tg.start_soon(m.set, "s1", "Title 1")
        tg.start_soon(m.set, "s2", "Title 2")

    # Lock ensures tasks run serially through set/persist
    assert order == ["start_persist", "end_persist", "start_persist", "end_persist"]
    assert m.get_all() == {"s1": "Title 1", "s2": "Title 2"}
