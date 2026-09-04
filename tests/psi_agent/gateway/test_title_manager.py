from __future__ import annotations

import anyio
import pytest

from psi_agent.runtime._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "Test Title")
    assert m.get_all() == {"s1": "Test Title"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_title_manager_concurrent_set() -> None:
    calls = []

    async def mock_persist() -> None:
        calls.append("persist")
        await anyio.sleep(0.01)

    m = TitleManager(_persist=mock_persist)

    async with anyio.create_task_group() as tg:
        tg.start_soon(m.set, "s1", "Title 1")
        tg.start_soon(m.set, "s2", "Title 2")

    assert len(m.get_all()) == 2
    assert len(calls) == 2
