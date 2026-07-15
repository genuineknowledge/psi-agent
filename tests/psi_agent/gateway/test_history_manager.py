from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._history_manager import HistoryManager


@pytest.mark.anyio
async def test_history_missing_file_returns_empty(tmp_path: str) -> None:
    hm = HistoryManager()
    assert await hm.get(str(tmp_path), "nope") == []


@pytest.mark.anyio
async def test_history_filters_roles_and_content(tmp_path: str) -> None:
    hm = HistoryManager()
    hist_dir = anyio.Path(str(tmp_path)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "system", "content": "sys"}',
            '{"role": "user", "content": "hi"}',
            '{"role": "assistant", "content": "\u4f60\u597d"}',
            '{"role": "tool", "content": "ignored"}',
            "not json",
            '{"role": "assistant", "content": ["multimodal"]}',
            '{"role": "assistant"}',
            "",
        ]
    )
    await (hist_dir / "s1.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path), "s1")

    assert result == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "\u4f60\u597d"},
    ]


@pytest.mark.anyio
async def test_history_hides_schedule_turns(tmp_path: str) -> None:
    hm = HistoryManager()
    hist_dir = anyio.Path(str(tmp_path)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "user", "content": "real chat"}',
            '{"role": "assistant", "content": "ok"}',
            '{"role": "user_schedule", "content": "# Heartbeat Task\\n..."}',
            '{"role": "assistant", "content": "HEARTBEAT_OK", "source": "schedule"}',
        ]
    )
    await (hist_dir / "s2.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path), "s2")

    assert result == [
        {"role": "user", "text": "real chat"},
        {"role": "assistant", "text": "ok"},
    ]
