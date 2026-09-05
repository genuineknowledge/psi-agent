from __future__ import annotations

import anyio
import pytest

from psi_agent.session.runtime_context import (
    get_session_id,
    get_user_message,
    mark_workflow_touched,
    runtime_scope,
    session_id_scope,
    workflow_was_touched,
)


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
        async with anyio.create_task_group() as tg:
            tg.start_soon(child)
    assert seen == ["sess-nested"]


def test_runtime_scope_binds_user_message_and_workflow_changes() -> None:
    touched: set[str] = set()
    assert get_user_message() == ""
    assert not workflow_was_touched("flows/review.workflow")

    with runtime_scope(
        session_id="session",
        user_message="  Create a review workflow  ",
        workflow_touched=touched,
    ):
        assert get_user_message() == "  Create a review workflow  "
        mark_workflow_touched("flows/review.workflow")
        assert workflow_was_touched("flows/review.workflow")

    assert touched == {"flows/review.workflow"}
    assert get_user_message() == ""
    assert not workflow_was_touched("flows/review.workflow")
