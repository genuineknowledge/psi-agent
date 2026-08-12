"""Shared cross-component protocol module."""

from __future__ import annotations

import json

import pytest

from psi_agent.protocol import (
    AUXILIARY_FINISH_REASONS,
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    SSE_DONE,
    ChatCompletionChunk,
    DeltaMessage,
    StreamChoice,
    is_auxiliary_finish,
    is_terminal_finish,
    make_compaction_signal,
    make_error_chunk,
    parse_sse_data,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('data: {"a":1}', '{"a":1}'),
        # The space after the colon is optional per the SSE spec.  Four call
        # sites used to require it and silently dropped whole frames without.
        ('data:{"a":1}', '{"a":1}'),
        ('data:   {"a":1}', '{"a":1}'),
        ("data: [DONE]", "[DONE]"),
        ("data:[DONE]", "[DONE]"),
        ("data:", ""),
        ("data: ", ""),
    ],
)
def test_parse_sse_data_extracts_payload(line: str, expected: str) -> None:
    assert parse_sse_data(line) == expected


@pytest.mark.parametrize("line", ["", "event: ping", ":heartbeat", "id: 1", "  data: x"])
def test_parse_sse_data_returns_none_for_non_data_lines(line: str) -> None:
    assert parse_sse_data(line) is None


@pytest.mark.parametrize(
    "value",
    [FINISH_REASON_STOP, FINISH_REASON_TOOL_CALLS, FINISH_REASON_ERROR, "length", "content_filter"],
)
def test_is_terminal_finish_accepts_terminal_and_unknown(value: str) -> None:
    """Unknown reasons count as terminal — only the auxiliary set is special."""
    assert is_terminal_finish(value) is True
    assert is_auxiliary_finish(value) is False


def test_compaction_is_auxiliary_not_terminal() -> None:
    assert is_auxiliary_finish(FINISH_REASON_COMPACTION_NEEDED) is True
    assert is_terminal_finish(FINISH_REASON_COMPACTION_NEEDED) is False


def test_none_is_neither_terminal_nor_auxiliary() -> None:
    """``None`` means the stream has not reported an end yet."""
    assert is_terminal_finish(None) is False
    assert is_auxiliary_finish(None) is False


def test_compaction_is_the_only_auxiliary_reason() -> None:
    assert frozenset({FINISH_REASON_COMPACTION_NEEDED}) == AUXILIARY_FINISH_REASONS


def test_make_error_chunk_matches_shape_used_by_all_three_producers() -> None:
    """Shape must stay byte-identical to what ai/router/session emitted before."""
    assert make_error_chunk("[Upstream Error]: boom") == {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "[Upstream Error]: boom"},
                "finish_reason": "error",
            }
        ],
    }


@pytest.mark.parametrize(
    "message",
    ["[Upstream Error]: boom", "[Router Error]: strategy failed", "bare agent error"],
)
def test_make_error_chunk_keeps_caller_prefix_verbatim(message: str) -> None:
    """Callers own their prefix; the helper never prepends one."""
    chunk = make_error_chunk(message)
    assert chunk["choices"][0]["delta"]["content"] == message


def test_make_compaction_signal_shape() -> None:
    assert make_compaction_signal(prompt_tokens=1234, threshold=1000) == {
        "id": "compaction",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "compaction_needed"}],
        "psi_compaction": {"needed": True, "prompt_tokens": 1234, "threshold": 1000},
    }


def test_sse_done_constant() -> None:
    assert SSE_DONE == "[DONE]"


def test_chat_completion_chunk_to_sse_round_trips() -> None:
    chunk = ChatCompletionChunk(
        id="chatcmpl-1",
        choices=[StreamChoice(delta=DeltaMessage(content="hi"), finish_reason="stop")],
    )
    sse = chunk.to_sse()
    assert sse.startswith("data: ")
    assert sse.endswith("\n\n")
    payload = parse_sse_data(sse.splitlines()[0])
    assert payload is not None
    assert json.loads(payload) == {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}],
    }


def test_delta_message_omits_unset_fields() -> None:
    assert DeltaMessage(content="x").to_dict() == {"content": "x"}
    assert DeltaMessage().to_dict() == {}


def test_stream_choice_omits_null_finish_reason() -> None:
    assert StreamChoice(delta=DeltaMessage(content="x")).to_dict() == {
        "index": 0,
        "delta": {"content": "x"},
    }
