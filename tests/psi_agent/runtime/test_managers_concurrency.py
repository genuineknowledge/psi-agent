from __future__ import annotations

import anyio
import pytest

from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_manager_concurrency() -> None:
    persist_count = 0

    async def mock_persist() -> None:
        nonlocal persist_count
        await anyio.sleep(0.01)
        persist_count += 1

    tm = TitleManager(_persist=mock_persist)

    async with anyio.create_task_group() as tg:
        tg.start_soon(tm.set, "s1", "Title 1")
        tg.start_soon(tm.set, "s2", "Title 2")
        tg.start_soon(tm.delete, "s1")

    assert persist_count == 3
    assert tm.get_all() == {"s2": "Title 2"}


@pytest.mark.anyio
async def test_summary_manager_concurrency() -> None:
    persist_count = 0

    async def mock_persist() -> None:
        nonlocal persist_count
        await anyio.sleep(0.01)
        persist_count += 1

    sm = SummaryManager(_persist=mock_persist)

    async with anyio.create_task_group() as tg:
        tg.start_soon(sm.set, "s1", "Summary 1")
        tg.start_soon(sm.set, "s2", "Summary 2")
        tg.start_soon(sm.delete, "s1")

    assert persist_count == 3
    assert sm.get_all() == {"s2": "Summary 2"}
