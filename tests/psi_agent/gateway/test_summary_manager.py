from __future__ import annotations

import pytest

from psi_agent.gateway._summary_manager import SummaryManager


@pytest.mark.anyio
async def test_summary_set_get_delete() -> None:
    m = SummaryManager()
    await m.set("s1", "为星辰科技写办公室剧本杀角色卡")
    assert m.get_all() == {"s1": "为星辰科技写办公室剧本杀角色卡"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent
