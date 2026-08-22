from __future__ import annotations

import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    tm = TitleManager()
    await tm.set("s1", "测试会话标题")
    assert tm.get_all() == {"s1": "测试会话标题"}
    await tm.delete("s1")
    assert tm.get_all() == {}
    await tm.delete("s1")  # idempotent
