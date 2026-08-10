from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._summary_manager import SummaryManager
from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "Stars Tech Script Card")
    assert m.get_all() == {"s1": "Stars Tech Script Card"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_title_manager_concurrency() -> None:
    called_count = 0

    async def mock_persist() -> None:
        nonlocal called_count
        called_count += 1
        await anyio.sleep(0.1)

    m = TitleManager(_persist=mock_persist)

    async def worker(sid: str, title: str) -> None:
        await m.set(sid, title)

    async with anyio.create_task_group() as tg:
        tg.start_soon(worker, "s1", "Title 1")
        tg.start_soon(worker, "s2", "Title 2")

    assert m.get_all() == {"s1": "Title 1", "s2": "Title 2"}
    assert called_count == 2


@pytest.mark.anyio
async def test_summary_manager_concurrency() -> None:
    called_count = 0

    async def mock_persist() -> None:
        nonlocal called_count
        called_count += 1
        await anyio.sleep(0.1)

    m = SummaryManager(_persist=mock_persist)

    async def worker(sid: str, summary: str) -> None:
        await m.set(sid, summary)

    async with anyio.create_task_group() as tg:
        tg.start_soon(worker, "s1", "Summary 1")
        tg.start_soon(worker, "s2", "Summary 2")

    assert m.get_all() == {"s1": "Summary 1", "s2": "Summary 2"}
    assert called_count == 2
