from __future__ import annotations

import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    persisted = False

    async def _on_persist() -> None:
        nonlocal persisted
        persisted = True

    m = TitleManager(_persist=_on_persist)
    await m.set("s1", "测试标题")
    assert await m.get_all() == {"s1": "测试标题"}
    assert persisted

    persisted = False
    await m.delete("s1")
    assert await m.get_all() == {}
    assert persisted

    persisted = False
    await m.delete("s1")  # idempotent
    assert not persisted
