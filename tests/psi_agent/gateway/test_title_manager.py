from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    persisted = 0

    async def _on_persist() -> None:
        nonlocal persisted
        persisted += 1

    m = TitleManager(_persist=_on_persist)
    await m.set("s1", "AI Title")
    assert m.get_all() == {"s1": "AI Title"}
    assert persisted == 1

    await m.delete("s1")
    assert m.get_all() == {}
    assert persisted == 2

    await m.delete("s1")  # idempotent, should not persist again
    assert persisted == 2


@pytest.mark.anyio
async def test_title_concurrent_sets() -> None:
    persisted = 0

    async def _on_persist() -> None:
        nonlocal persisted
        # Sleep slightly to allow other tasks to try to run and hit the lock
        await anyio.sleep(0.01)
        persisted += 1

    m = TitleManager(_persist=_on_persist)

    async def _set_title(sid: str, val: str) -> None:
        await m.set(sid, val)

    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(_set_title, f"session-{i}", f"Title {i}")

    assert persisted == 10
    all_titles = m.get_all()
    assert len(all_titles) == 10
    for i in range(10):
        assert all_titles[f"session-{i}"] == f"Title {i}"
