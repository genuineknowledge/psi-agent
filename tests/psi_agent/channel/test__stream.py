from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import aclosing

import anyio
import pytest

from psi_agent.channel._errors import ChannelError
from psi_agent.channel._stream import IDLE, StreamBuffer, iter_sse_events


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


def test_buffer_reasoning_kind_switch_does_not_merge():
    """Same reasoning slot, different provenance keys must not coalesce."""
    b = StreamBuffer(10.0)
    assert b.switch("reasoning:thinking") == []
    assert b.append("plan") == []
    assert b.switch("reasoning:tool_call") == [("reasoning:thinking", "plan")]
    assert b.append("call") == []
    assert b.switch("reasoning:tool_result") == [("reasoning:tool_call", "call")]
    assert b.append("done") == []
    assert b.flush() == [("reasoning:tool_result", "done")]


def test_buffer_flush_empty_returns_empty():
    b = StreamBuffer(10.0)
    assert b.flush() == []


def test_buffer_drain_if_idle_emits_tail_before_stream_end():
    """The tail buffered before an upstream pause must not wait for [DONE]."""
    b = StreamBuffer(10.0)
    b.switch("text")
    assert b.append("tail") == []
    assert b.drain_if_idle() == [("text", "tail")]
    # Already drained → stream end has nothing left to flush.
    assert b.flush() == []


def test_buffer_drain_if_idle_empty_is_noop():
    """Repeated idle ticks with nothing buffered must not emit empty blocks."""
    b = StreamBuffer(10.0)
    b.switch("text")
    assert b.drain_if_idle() == []
    assert b.drain_if_idle() == []


def test_buffer_drain_if_idle_resets_window():
    """After an idle drain the next append starts a fresh window, not an expired one."""
    b = StreamBuffer(10.0)
    b.switch("text")
    b.append("a")
    assert b.drain_if_idle() == [("text", "a")]
    # Window restarted → this keeps buffering instead of emitting immediately.
    assert b.append("b") == []
    assert b.flush() == [("text", "b")]


def test_buffer_drain_if_idle_keeps_reasoning_kind():
    """An idle drain must carry the reasoning provenance, not degrade to text."""
    b = StreamBuffer(10.0)
    b.switch("reasoning:thinking")
    b.append("half a thought")
    assert b.drain_if_idle() == [("reasoning:thinking", "half a thought")]


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
    with pytest.raises(ChannelError, match="Expected exactly 1 choice"):
        _ = [d async for d in iter_sse_events(_alines(_sse(chunk)))]


@pytest.mark.anyio
async def test_sse_raises_on_finish_error():
    chunk = {"choices": [{"delta": {"content": "[Upstream Error]: boom"}, "finish_reason": "error"}]}
    with pytest.raises(ChannelError, match="Upstream Error"):
        _ = [d async for d in iter_sse_events(_alines(_sse(chunk)))]


@pytest.mark.anyio
async def test_sse_null_delta_coerced_to_empty_dict():
    chunk = {"choices": [{"index": 0, "delta": None}]}
    events = [d async for d in iter_sse_events(_alines(_sse(chunk)))]
    assert events == [{}]


@pytest.mark.anyio
async def test_sse_missing_delta_coerced_to_empty_dict():
    chunk = {"choices": [{"index": 0}]}
    events = [d async for d in iter_sse_events(_alines(_sse(chunk)))]
    assert events == [{}]


@pytest.mark.anyio
async def test_sse_skips_non_dict_choice():
    bad = {"choices": ["not-a-dict"]}
    good = {"choices": [{"delta": {"content": "ok"}}]}
    events = [d async for d in iter_sse_events(_alines(_sse(bad), _sse(good)))]
    assert events == [{"content": "ok"}]


@pytest.mark.anyio
async def test_sse_skips_non_list_choices():
    bad = {"choices": {"unexpected": "shape"}}
    good = {"choices": [{"delta": {"content": "ok"}}]}
    events = [d async for d in iter_sse_events(_alines(_sse(bad), _sse(good)))]
    assert events == [{"content": "ok"}]


class _StallingReader:
    """A *resumable* stalling line source, modelling aiohttp's ``StreamReader``.

    Deliberately class-based rather than an async generator: with ``idle_timeout``
    armed the pending read gets cancelled, and only a class-based iterator survives
    that and resumes on the next call. An async generator would be finalized by the
    cancellation and silently drop the rest of the stream — which is precisely why
    ``iter_sse_events`` documents that its source must be resumable.
    """

    def __init__(self, head: bytes, stall: float, tail: bytes) -> None:
        self._items = [head, tail]
        self._stall = stall
        self._index = 0
        self._stalled = False

    def __aiter__(self) -> _StallingReader:
        return self

    async def __anext__(self) -> bytes:
        if self._index == 1 and not self._stalled:
            self._stalled = True
            await anyio.sleep(self._stall)
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


async def _stalling_lines(head: bytes, stall: float, tail: bytes) -> AsyncIterator[bytes]:
    yield head
    await anyio.sleep(stall)
    yield tail


@pytest.mark.anyio
async def test_sse_reports_idle_while_upstream_is_silent():
    """Silence past ``idle_timeout`` surfaces as IDLE without dropping later deltas."""
    lines = _StallingReader(
        _sse({"choices": [{"delta": {"content": "before"}}]}),
        0.5,
        _sse({"choices": [{"delta": {"content": "after"}}]}),
    )
    events = [d async for d in iter_sse_events(lines, idle_timeout=0.1)]

    assert IDLE in events, "expected at least one idle report during the stall"
    # The stall must not cost any real delta: cancelling a read on a resumable
    # (class-based) reader leaves it intact, as aiohttp's StreamReader is.
    assert [d for d in events if d is not IDLE] == [{"content": "before"}, {"content": "after"}]


@pytest.mark.anyio
async def test_sse_idle_timeout_truncates_a_generator_source():
    """Documents *why* ``idle_timeout`` demands a resumable reader (not a generator).

    An async generator gets finalized when its pending ``__anext__`` is cancelled, so
    arming the timeout over one silently loses everything after the stall. Production
    passes aiohttp's ``StreamReader``, which is resumable; this test exists so the
    constraint is executable rather than a docstring claim, and so anyone who later
    swaps the source to a generator sees it fail here.
    """
    lines = _stalling_lines(
        _sse({"choices": [{"delta": {"content": "before"}}]}),
        0.5,
        _sse({"choices": [{"delta": {"content": "after"}}]}),
    )
    events = [d async for d in iter_sse_events(lines, idle_timeout=0.1)]

    # "after" is gone — the generator was finalized by the cancellation.
    assert [d for d in events if d is not IDLE] == [{"content": "before"}]


@pytest.mark.anyio
async def test_sse_idle_timeout_zero_never_reports_idle():
    """The default keeps the plain read path — no IDLE, byte-for-byte old behaviour."""
    lines = _stalling_lines(
        _sse({"choices": [{"delta": {"content": "a"}}]}),
        0.3,
        _sse({"choices": [{"delta": {"content": "b"}}]}),
    )
    events = [d async for d in iter_sse_events(lines, idle_timeout=0.0)]

    assert events == [{"content": "a"}, {"content": "b"}]


@pytest.mark.anyio
async def test_sse_idle_does_not_fire_on_active_stream():
    """Deltas arriving faster than the timeout produce no idle reports."""
    chunk = {"choices": [{"delta": {"content": "x"}}]}
    events = [d async for d in iter_sse_events(_alines(_sse(chunk), _sse(chunk)), idle_timeout=5.0)]

    assert events == [{"content": "x"}, {"content": "x"}]


@pytest.mark.anyio
async def test_sse_idle_timeout_still_honours_done():
    """[DONE] terminates normally even with the idle timeout armed."""
    lines = _alines(_sse({"choices": [{"delta": {"content": "a"}}]}), b"data: [DONE]", _sse({"choices": [{}]}))
    events = [d async for d in iter_sse_events(lines, idle_timeout=5.0)]

    assert events == [{"content": "a"}]


@pytest.mark.anyio
async def test_sse_idle_timeout_still_raises_on_finish_error():
    """Error propagation is unchanged on the idle path (stays a bare ChannelError)."""
    chunk = {"choices": [{"delta": {"content": "[Upstream Error]: boom"}, "finish_reason": "error"}]}
    with pytest.raises(ChannelError, match="Upstream Error"):
        _ = [d async for d in iter_sse_events(_alines(_sse(chunk)), idle_timeout=5.0)]


@pytest.mark.anyio
async def test_sse_idle_early_break_closes_cleanly():
    """Breaking out mid-stall must not raise from the cancel scope.

    Guards the defect that sank the first implementation: a cancel scope entered
    around a ``yield`` can be exited by a different task during ``aclose()``, which
    anyio rejects with ``RuntimeError: Attempted to exit cancel scope in a different
    task``. Here the scope must be fully closed before each yield.
    """
    lines = _stalling_lines(
        _sse({"choices": [{"delta": {"content": "head"}}]}),
        30.0,
        b"data: [DONE]",
    )
    async with aclosing(iter_sse_events(lines, idle_timeout=0.1)) as events:
        async for delta in events:
            if delta is IDLE:
                break
