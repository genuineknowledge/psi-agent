"""Keepalive must not cancel the upstream chat chunk generator."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast

import anyio
import pytest
from aiohttp import web

from psi_agent.gateway.server import _write_chat_sse_with_keepalive


class _FakeResp:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.writes.append(data)


@pytest.mark.anyio
async def test_keepalive_does_not_cancel_slow_chunk_generator() -> None:
    """Regression: fail_after(__anext__) used to tear down ChannelCore on idle."""
    events: list[str] = []

    async def slow_chunks() -> AsyncGenerator[dict[str, Any]]:
        events.append("start")
        await anyio.sleep(0.35)
        events.append("yield")
        yield {"type": "text", "text": "hi"}
        events.append("done")

    resp = _FakeResp()
    await _write_chat_sse_with_keepalive(
        cast(web.StreamResponse, resp),
        slow_chunks(),
        session_id="test",
        keepalive_sec=0.1,
    )

    assert events == ["start", "yield", "done"]
    assert any(w.startswith(b": keepalive") for w in resp.writes)
    assert any(b"hi" in w and b"text" in w for w in resp.writes)
