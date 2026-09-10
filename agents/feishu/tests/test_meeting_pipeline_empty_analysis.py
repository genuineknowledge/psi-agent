from __future__ import annotations

# Local meeting tools intentionally load from the agent package rather than an installed package.
# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meeting_pipeline_run as pipeline  # ty: ignore[unresolved-import]

from psi_agent.session.protocol import AiDelta


class _FakeAiClient:
    """Dummy AiClient whose stream() replays a fixed delta list.

    Accepts either the real constructor shape ``AiClient(ai_socket)`` (deltas
    taken from ``_DEFAULT_DELTAS``) or a direct ``_FakeAiClient(deltas)``.
    """

    _DEFAULT_DELTAS: ClassVar[list[AiDelta]] = []

    def __init__(self, arg: Any, *, deltas: list[AiDelta] | None = None) -> None:
        self._deltas = deltas if deltas is not None else (self._DEFAULT_DELTAS if isinstance(arg, str) else arg)

    async def stream(self, request_body: dict[str, Any]):
        for delta in self._deltas:
            yield delta


def _reasoning_only_deltas() -> list[AiDelta]:
    """Thinking-only stream: reasoning is present, content never arrives."""
    return [AiDelta(reasoning="thinking..." * 10), AiDelta(finish_reason="stop")]


def _normal_deltas() -> list[AiDelta]:
    payload = json.dumps(
        {"analysis_text": "text", "meeting_summary": "summary", "positive_negative_overview": "overview"},
        ensure_ascii=False,
    )
    return [AiDelta(content=payload), AiDelta(finish_reason="stop")]


def test_analysis_has_content_treats_placeholder_as_empty() -> None:
    assert not pipeline._analysis_has_content(
        {
            "analysis_text": pipeline._EMPTY_ANALYSIS_MARKER,
            "meeting_summary": pipeline._EMPTY_ANALYSIS_MARKER,
            "positive_negative_overview": pipeline._EMPTY_ANALYSIS_MARKER,
        }
    )
    assert not pipeline._analysis_has_content(
        {"analysis_text": "", "meeting_summary": "", "positive_negative_overview": ""}
    )
    assert pipeline._analysis_has_content(
        {"analysis_text": "body", "meeting_summary": "", "positive_negative_overview": ""}
    )


@pytest.mark.anyio
async def test_stream_ai_json_attaches_diagnostics_when_content_empty() -> None:
    result = await pipeline._stream_ai_json(
        _FakeAiClient(_reasoning_only_deltas()),
        system_prompt="sp",
        user_content="u",
    )
    stream_info = result["_ai_stream"]
    assert stream_info["content_chars"] == 0
    assert stream_info["reasoning_chars"] > 0
    assert stream_info["finish_reason"] == "stop"
    assert result["analysis_text"] == ""


@pytest.mark.anyio
async def test_stream_ai_json_records_content_when_present() -> None:
    result = await pipeline._stream_ai_json(
        _FakeAiClient(_normal_deltas()),
        system_prompt="sp",
        user_content="u",
    )
    assert result["_ai_stream"]["content_chars"] > 0
    assert result["_ai_stream"]["reasoning_chars"] == 0
    assert result["analysis_text"] == "text"


@pytest.mark.anyio
async def test_analyze_counts_empty_calls_and_returns_empty_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "current_tool_ai_socket", lambda: "fake-socket")
    monkeypatch.setattr(pipeline, "AiClient", _FakeAiClient)
    monkeypatch.setattr(_FakeAiClient, "_DEFAULT_DELTAS", _reasoning_only_deltas())
    stats: dict[str, int] = {}

    analysis = await pipeline._analyze_meeting_transcript(
        "short transcript, one chunk",
        stats=stats,
    )

    assert not pipeline._analysis_has_content(analysis)
    assert stats["ai_calls"] == 1
    assert stats["ai_content_chars"] == 0
    assert stats["ai_reasoning_chars"] > 0
    assert stats["ai_empty_calls"] == 1


@pytest.mark.anyio
async def test_analyze_returns_content_when_model_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "current_tool_ai_socket", lambda: "fake-socket")
    monkeypatch.setattr(pipeline, "AiClient", _FakeAiClient)
    monkeypatch.setattr(_FakeAiClient, "_DEFAULT_DELTAS", _normal_deltas())
    stats: dict[str, int] = {}

    analysis = await pipeline._analyze_meeting_transcript(
        "short transcript, one chunk",
        stats=stats,
    )

    assert pipeline._analysis_has_content(analysis)
    assert stats.get("ai_empty_calls", 0) == 0
    assert analysis["meeting_summary"] == "summary"


def test_analysis_stats_entry_shape() -> None:
    entry = pipeline._analysis_stats_entry({"ai_calls": 4, "ai_input_chars": 45207, "ai_empty_calls": 1})
    assert entry == {
        "ai_calls": 4,
        "ai_input_chars": 45207,
        "ai_content_chars": 0,
        "ai_reasoning_chars": 0,
        "ai_empty_calls": 1,
    }
