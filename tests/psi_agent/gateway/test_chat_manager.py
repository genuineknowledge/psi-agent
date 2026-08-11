from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent._router_status import RouterStatus
from psi_agent.channel._types import (
    InputChunk,
    OutputChunk,
    ReasoningChunk,
    RouterStatusChunk,
    TextChunk,
)
from psi_agent.gateway import _chat_manager
from psi_agent.gateway._chat_manager import ChatManager

TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` to *tmp_path*.

    ``_downloads_path`` resolves the home directory through ``Path.home()``,
    which reads ``USERPROFILE`` on Windows and ``HOME`` elsewhere - patching
    only ``HOME`` left these tests writing into the developer's real Downloads
    folder (and failing the location assertion on Windows).
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.mark.anyio
async def test__save_upload_writes_to_downloads(fake_home: Path) -> None:
    cm = ChatManager()

    path = await cm._save_upload("hello.png", b"payload")

    assert os.path.basename(path) == "hello.png"
    assert str(fake_home) in path
    assert await anyio.Path(path).read_bytes() == b"payload"


@pytest.mark.anyio
async def test__save_upload_sanitizes_filename(fake_home: Path) -> None:
    cm = ChatManager()

    path = await cm._save_upload("../../evil.txt", b"x")

    assert os.path.basename(path) == "evil.txt"
    assert ".." not in path
    assert str(fake_home) in path
    assert await anyio.Path(path).exists()


@pytest.mark.anyio
async def test__file_blob_returns_warning_without_leaking_path(tmp_path: Path) -> None:
    missing = str(tmp_path / "private" / "missing.txt")

    event = await ChatManager()._file_blob(missing)

    assert event == {
        "type": "error",
        "severity": "warning",
        "code": "output_file_unavailable",
        "error": "Generated file could not be read",
    }
    assert missing not in str(event)


@pytest.mark.anyio
async def test_handle_preserves_router_status_order_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = RouterStatus(
        trace_id="123e4567-e89b-12d3-a456-426614174000",
        mode="aggregation",
        phase="collecting",
        depth=2,
        completed=1,
        total=3,
        degraded=True,
    )
    output_chunks: list[OutputChunk] = [
        TextChunk(text="before"),
        RouterStatusChunk(status=status),
        ReasoningChunk(text="after", kind="tool_result"),
    ]
    observed: dict[str, Any] = {}

    class FakeChannelCore:
        def __init__(self, *, session_socket: str, interval: float) -> None:
            observed["session_socket"] = session_socket
            observed["interval"] = interval

        async def __aenter__(self) -> FakeChannelCore:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            chunks: list[InputChunk],
            *,
            trace_id: str | None = None,
        ) -> AsyncIterator[OutputChunk]:
            observed["input_chunks"] = chunks
            observed["trace_id"] = trace_id
            for chunk in output_chunks:
                yield chunk

    monkeypatch.setattr(_chat_manager, "ChannelCore", FakeChannelCore)

    events = [
        event
        async for event in ChatManager().handle(
            "unused-channel-socket",
            {"chunks": [{"type": "text", "text": "hello"}]},
            trace_id=TRACE_ID,
        )
    ]

    assert observed == {
        "session_socket": "unused-channel-socket",
        "interval": 0.0,
        "input_chunks": [TextChunk(text="hello")],
        "trace_id": TRACE_ID,
    }
    assert events == [
        {"type": "text", "trace_id": TRACE_ID, "text": "before"},
        {
            "type": "router_status",
            "version": 1,
            "trace_id": "123e4567-e89b-12d3-a456-426614174000",
            "mode": "aggregation",
            "phase": "collecting",
            "depth": 2,
            "completed": 1,
            "total": 3,
            "degraded": True,
        },
        {"type": "reasoning", "trace_id": TRACE_ID, "text": "after", "kind": "tool_result"},
    ]


@pytest.mark.anyio
async def test_handle_closes_channel_stream_after_early_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = RouterStatus(
        trace_id="123e4567-e89b-12d3-a456-426614174000",
        mode="routing",
        phase="selecting",
    )
    observed = {"stream_closed": False}

    class FakeChannelCore:
        def __init__(self, *, session_socket: str, interval: float) -> None:
            pass

        async def __aenter__(self) -> FakeChannelCore:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            chunks: list[InputChunk],
            *,
            trace_id: str | None = None,
        ) -> AsyncIterator[OutputChunk]:
            assert trace_id == TRACE_ID
            try:
                yield RouterStatusChunk(status=status)
                yield TextChunk(text="must not be consumed")
            finally:
                observed["stream_closed"] = True

    monkeypatch.setattr(_chat_manager, "ChannelCore", FakeChannelCore)

    events = ChatManager().handle(
        "unused-channel-socket",
        {"chunks": [{"type": "text", "text": "hello"}]},
        trace_id=TRACE_ID,
    )
    async with aclosing(events) as stream:
        first = await anext(stream)
        assert first == {"type": "router_status", **status.to_dict()}

    assert observed["stream_closed"] is True
