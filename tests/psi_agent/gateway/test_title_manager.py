from __future__ import annotations

import pytest

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "Test Title")
    assert m.get_all() == {"s1": "Test Title"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent
