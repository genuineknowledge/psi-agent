from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "A Great Conversation Title")
    assert m.get_all() == {"s1": "A Great Conversation Title"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_title_manager_concurrency() -> None:
    m = TitleManager()

    async def task(i: int) -> None:
        await m.set(f"session_{i}", f"Title {i}")
        await anyio.sleep(0.001)
        await m.delete(f"session_{i}")

    async with anyio.create_task_group() as tg:
        for i in range(50):
            tg.start_soon(task, i)

    assert m.get_all() == {}
