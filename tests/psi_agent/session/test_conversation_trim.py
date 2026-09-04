from __future__ import annotations

import pytest

from psi_agent.session.conversation import Conversation


@pytest.mark.anyio
async def test_trim_after_removes_messages() -> None:
    conv = Conversation(
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "what's up"},
        ]
    )
    conv.trim_after(0)
    assert len(conv.messages) == 1
    assert conv.messages[0]["role"] == "system"


@pytest.mark.anyio
async def test_trim_after_keeps_up_to_index() -> None:
    conv = Conversation(
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
    )
    conv.trim_after(1)
    assert len(conv.messages) == 2
    assert conv.messages[0]["role"] == "system"
    assert conv.messages[1]["role"] == "user"


@pytest.mark.anyio
async def test_trim_after_rollback_restores() -> None:
    conv = Conversation(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
    )
    conv.trim_after(0)
    assert len(conv.messages) == 1
    conv.rollback()
    assert len(conv.messages) == 3


@pytest.mark.anyio
async def test_truncate_to_keeps_prefix() -> None:
    conv = Conversation(
        messages=[
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
    )
    conv.truncate_to(1)
    assert len(conv.messages) == 1
    assert conv.messages[0]["role"] == "system"


@pytest.mark.anyio
async def test_trim_after_empty_is_noop() -> None:
    conv = Conversation()
    conv.trim_after(0)
    assert conv.messages == []


@pytest.mark.anyio
async def test_trim_after_index_beyond_length() -> None:
    conv = Conversation(messages=[{"role": "user", "content": "hi"}])
    conv.trim_after(5)
    assert len(conv.messages) == 1
