from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from psi_agent.channel._stream import StreamBuffer, iter_sse_events


def test_buffer_merges_within_interval():
    b = StreamBuffer(10.0)
    assert b.switch("text") == []
    assert b.append("a") == []
    assert b.append("b") == []
    assert b.flush() == [("text", "ab")]


def test_buffer_interval_zero_flushes_each_append():
    b = StreamBuffer(0.0)
    b.switch("text")
    assert b.append("a") == [("text", "a")]
    assert b.append("b") == [("text", "b")]
    assert b.flush() == []


def test_buffer_type_switch_flushes_previous():
    b = StreamBuffer(10.0)
    b.switch("reasoning")
    b.append("think")
    assert b.switch("text") == [("reasoning", "think")]
    b.append("answer")
    assert b.flush() == [("text", "answer")]


def test_buffer_flush_empty_returns_empty():
    b = StreamBuffer(10.0)
    assert b.flush() == []


async def _alines(*items: bytes) -> AsyncIterator[bytes]:
    for it in items:
        yield it


def _sse(obj: object) -> bytes:
    return f"data: {json.dumps(obj)}".encode()


@pytest.mark.anyio
async def test_sse_yields_delta():
    chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}
    events = [d async for d in iter_sse_events(_alines(_sse(chunk)))]
    assert events == [{"content": "hi"}]


@pytest.mark.anyio
async def test_sse_done_terminates():
    chunk = {"choices": [{"index": 0, "delta": {"content": "a"}}]}
    extra = {"choices": [{"delta": {"content": "ignored"}}]}
    lines = _alines(_sse(chunk), b"data: [DONE]", _sse(extra))
    events = [d async for d in iter_sse_events(lines)]
    assert events == [{"content": "a"}]


@pytest.mark.anyio
async def test_sse_skips_malformed():
    chunk = {"choices": [{"delta": {"content": "ok"}}]}
    events = [d async for d in iter_sse_events(_alines(b"data: not-json", _sse(chunk)))]
    assert events == [{"content": "ok"}]


@pytest.mark.anyio
async def test_sse_skips_heartbeat_zero_choices():
    chunk = {"choices": [{"delta": {"content": "ok"}}]}
    events = [d async for d in iter_sse_events(_alines(_sse({"choices": []}), _sse(chunk)))]
    assert events == [{"content": "ok"}]


@pytest.mark.anyio
async def test_sse_skips_non_data_lines():
    chunk = {"choices": [{"delta": {"content": "ok"}}]}
    events = [d async for d in iter_sse_events(_alines(b"", b": comment", _sse(chunk)))]
    assert events == [{"content": "ok"}]


@pytest.mark.anyio
async def test_sse_rejects_multiple_choices():
    chunk = {"choices": [{"delta": {"content": "a"}}, {"delta": {"content": "b"}}]}
    with pytest.raises(Exception, match="Expected exactly 1 choice"):
        _ = [d async for d in iter_sse_events(_alines(_sse(chunk)))]


@pytest.mark.anyio
async def test_sse_raises_on_finish_error():
    chunk = {"choices": [{"delta": {"content": "[Upstream Error]: boom"}, "finish_reason": "error"}]}
    with pytest.raises(Exception, match="Upstream Error"):
        _ = [d async for d in iter_sse_events(_alines(_sse(chunk)))]
