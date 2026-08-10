from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from psi_agent._router_status import RouterStatus
from psi_agent.channel._errors import ChannelError
from psi_agent.channel._stream import StreamBuffer, iter_sse_events

_TRACE_ID = "12345678-1234-5678-1234-567812345678"


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


def test_buffer_flush_resets_interval_window(monkeypatch):
    times = iter((100.0, 100.0, 200.0, 200.0))
    monkeypatch.setattr("psi_agent.channel._stream.time.monotonic", lambda: next(times))
    b = StreamBuffer(10.0)
    b.switch("text")
    assert b.append("before") == []
    assert b.flush() == [("text", "before")]

    b.switch("text")
    assert b.append("after") == []
    assert b.flush() == [("text", "after")]


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
async def test_sse_validates_and_normalizes_router_status():
    status = RouterStatus(trace_id=_TRACE_ID, mode="routing", phase="selecting")

    events = [d async for d in iter_sse_events(_alines(_sse(status.to_event())))]

    assert events == [{"router_status": status}]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "chunk",
    [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "router_status": {
                            "version": 2,
                            "trace_id": _TRACE_ID,
                            "mode": "routing",
                            "phase": "selecting",
                            "depth": 0,
                        }
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "router_status": {
                            "version": 1,
                            "trace_id": _TRACE_ID,
                            "mode": "routing",
                            "phase": "selecting",
                            "depth": 0,
                        },
                        "content": "candidate-secret",
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "router_status": {
                            "version": 1,
                            "trace_id": _TRACE_ID,
                            "mode": "routing",
                            "phase": "selecting",
                            "depth": 0,
                        }
                    },
                    "finish_reason": "candidate-secret",
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "router_status": {
                            "version": 1,
                            "trace_id": _TRACE_ID,
                            "mode": "routing",
                            "phase": "candidate-secret",
                            "depth": 0,
                        }
                    },
                    "finish_reason": None,
                }
            ]
        },
    ],
)
async def test_sse_rejects_invalid_router_status_without_echoing_fields(chunk):
    with pytest.raises(ChannelError, match="Invalid router_status event") as exc_info:
        _ = [d async for d in iter_sse_events(_alines(_sse(chunk)))]

    assert "candidate-secret" not in str(exc_info.value)


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
