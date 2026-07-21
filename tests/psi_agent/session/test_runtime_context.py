from __future__ import annotations

import pytest

from psi_agent.session.runtime_context import get_session_id, session_id_scope


def test_session_id_scope_sets_and_resets() -> None:
    assert get_session_id() == ""
    with session_id_scope("abc123"):
        assert get_session_id() == "abc123"
    assert get_session_id() == ""


@pytest.mark.anyio
async def test_session_id_scope_visible_in_nested_task() -> None:
    seen: list[str] = []

    async def child() -> None:
        seen.append(get_session_id())

    with session_id_scope("sess-nested"):
        import anyio

        async with anyio.create_task_group() as tg:
            tg.start_soon(child)
    assert seen == ["sess-nested"]
