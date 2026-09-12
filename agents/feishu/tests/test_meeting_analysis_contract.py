"""会议分析输出契约判据: 三个正文字段必须是自然语言字符串。

事故背景 (2026-09-11): 模型把 ``meeting_summary`` 写成嵌套对象, ``_normalize_analysis``
用 ``str()`` 兜底, 于是带单引号的 Python 字面量被原样发进会议群。本文件钉住三层防御:
提示词的类型契约、展平归一、解析后的校验/重试/降级与告警标记。
"""

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


class _ScriptedAiClient:
    """AiClient 假体: 按调用次序回放脚本化的输出 (每次 stream 取一条脚本)。"""

    _SCRIPT: ClassVar[list[list[AiDelta]]] = []

    def __init__(self, arg: Any) -> None:
        self._script = list(type(self)._SCRIPT)

    async def stream(self, request_body: dict[str, Any]):
        deltas = self._script.pop(0) if self._script else []
        for delta in deltas:
            yield delta


def _deltas(payload: Any) -> list[AiDelta]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return [AiDelta(content=text), AiDelta(finish_reason="stop")]


_OBJECT_SUMMARY = {"meeting_type": "周中对齐会", "participants": ["周熠", "赵胜迪"]}
_BAD_PAYLOAD = {
    "analysis_text": "本场正文",
    "meeting_summary": _OBJECT_SUMMARY,
    "positive_negative_overview": "概览",
}
_GOOD_PAYLOAD = {
    "analysis_text": "本场正文",
    "meeting_summary": "本场 SOP 判定: 符合",
    "positive_negative_overview": "概览",
}


def test_flatten_value_renders_objects_as_readable_text() -> None:
    text = pipeline._flatten_value(_OBJECT_SUMMARY)
    assert "meeting_type: 周中对齐会" in text
    assert "- 周熠" in text
    assert "{'" not in text and "'}" not in text


def test_normalize_analysis_never_emits_python_repr() -> None:
    analysis = pipeline._normalize_analysis(_BAD_PAYLOAD)
    summary = analysis["meeting_summary"]
    assert "{'" not in summary, summary
    assert "meeting_type: 周中对齐会" in summary
    assert not pipeline._looks_like_python_repr(summary)


def test_analysis_prompt_requires_string_values() -> None:
    prompt = pipeline._analysis_system_prompt(None)
    assert "都必须是自然语言字符串" in prompt
    assert "不得写成对象" in prompt


def test_non_string_value_keys_flags_objects_numbers_and_aliases() -> None:
    assert pipeline._non_string_value_keys(_BAD_PAYLOAD) == ["meeting_summary"]
    assert pipeline._non_string_value_keys(_GOOD_PAYLOAD) == []
    assert pipeline._non_string_value_keys({"minutes": _OBJECT_SUMMARY}) == ["meeting_summary"]
    assert pipeline._non_string_value_keys({"meeting_summary": "ok", "positive_negative_overview": 3}) == [
        "positive_negative_overview"
    ]


def test_degrade_python_repr_replaces_repr_text_with_analysis_text() -> None:
    issues: list[str] = []
    analysis = pipeline._degrade_python_repr(
        {
            "analysis_text": "完整正文",
            "meeting_summary": "{'meeting_type': '周中对齐会'}",
            "positive_negative_overview": "概览",
        },
        issues,
    )
    assert analysis["meeting_summary"] == "完整正文"
    assert analysis["positive_negative_overview"] == "概览"
    assert issues == ["meeting_summary_downgraded_to_analysis_text"]


@pytest.mark.anyio
async def test_analyze_retries_once_when_value_is_not_a_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """值写成对象时纠正重试一次; 重试成功后按正常字符串产出。"""
    monkeypatch.setattr(pipeline, "current_tool_ai_socket", lambda: "fake-socket")
    monkeypatch.setattr(pipeline, "AiClient", _ScriptedAiClient)
    monkeypatch.setattr(_ScriptedAiClient, "_SCRIPT", [_deltas(_BAD_PAYLOAD), _deltas(_GOOD_PAYLOAD)])
    stats: dict[str, int] = {}
    issues: list[str] = []

    analysis = await pipeline._analyze_meeting_transcript("短转写一段", stats=stats, issues=issues)

    assert stats["ai_calls"] == 2
    assert "analysis_value_not_string:meeting_summary" in issues
    assert "analysis_value_recovered_after_retry" in issues
    assert analysis["meeting_summary"] == "本场 SOP 判定: 符合"


@pytest.mark.anyio
async def test_analyze_degrades_and_flags_when_retry_still_not_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """两次都不合格: 不再重试, 展平后仍投递, 但记 issue 供告警。"""
    monkeypatch.setattr(pipeline, "current_tool_ai_socket", lambda: "fake-socket")
    monkeypatch.setattr(pipeline, "AiClient", _ScriptedAiClient)
    monkeypatch.setattr(_ScriptedAiClient, "_SCRIPT", [_deltas(_BAD_PAYLOAD), _deltas(_BAD_PAYLOAD)])
    stats: dict[str, int] = {}
    issues: list[str] = []

    analysis = await pipeline._analyze_meeting_transcript("短转写一段", stats=stats, issues=issues)

    assert stats["ai_calls"] == 2, "只允许纠正重试一次"
    assert "analysis_value_not_string:meeting_summary" in issues
    assert "analysis_value_still_not_string:meeting_summary" in issues
    assert "{'" not in analysis["meeting_summary"]
    assert "meeting_type: 周中对齐会" in analysis["meeting_summary"]


@pytest.mark.anyio
async def test_analyze_keeps_string_contract_without_extra_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """合规输出不触发重试, 也不产生任何 issue。"""
    monkeypatch.setattr(pipeline, "current_tool_ai_socket", lambda: "fake-socket")
    monkeypatch.setattr(pipeline, "AiClient", _ScriptedAiClient)
    monkeypatch.setattr(_ScriptedAiClient, "_SCRIPT", [_deltas(_GOOD_PAYLOAD)])
    stats: dict[str, int] = {}
    issues: list[str] = []

    analysis = await pipeline._analyze_meeting_transcript("短转写一段", stats=stats, issues=issues)

    assert stats["ai_calls"] == 1
    assert issues == []
    assert analysis["meeting_summary"] == "本场 SOP 判定: 符合"
