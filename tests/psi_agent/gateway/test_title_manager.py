from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "Title 1")
    assert m.get_all() == {"s1": "Title 1"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_title_concurrent_set_delete() -> None:
    m = TitleManager()

    async def worker(session_id: str, text: str) -> None:
        await m.set(session_id, text)
        await m.delete(session_id)

    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(worker, f"s{i}", f"title-{i}")

    assert m.get_all() == {}
