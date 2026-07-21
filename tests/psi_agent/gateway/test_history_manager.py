from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._history_manager import HistoryManager


@pytest.mark.anyio
async def test_history_missing_file_returns_empty(tmp_path: str) -> None:
    hm = HistoryManager()
    assert await hm.get(str(tmp_path), "nope") == []


@pytest.mark.anyio
async def test_history_filters_roles_kind_and_markers(tmp_path: str) -> None:
    hm = HistoryManager()
    hist_dir = anyio.Path(str(tmp_path)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "system", "content": "sys"}',
            '{"role": "user", "content": "hi", "kind": "chat"}',
            '{"role": "assistant", "content": "\u4f60\u597d", "kind": "chat"}',
            '{"role": "user", "content": "# Heartbeat Task", "kind": "schedule.silent"}',
            '{"role": "assistant", "content": "HEARTBEAT_OK", "kind": "schedule.silent"}',
            '{"role": "assistant", "content": "\u65e5\u62a5", "kind": "schedule.display"}',
            '{"role": "user", "content": "\u770b\u56fe\\n[RECV:/tmp/a.png]", "kind": "chat"}',
            '{"role": "tool", "content": "ignored"}',
            "not json",
            '{"role": "assistant", "content": ["multimodal"]}',
            '{"role": "assistant"}',
            '{"role": "assistant", "content": "HEARTBEAT_OK"}',
            "",
        ]
    )
    await (hist_dir / "s1.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path), "s1")

    assert result == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "\u4f60\u597d"},
        {"role": "assistant", "text": "\u65e5\u62a5", "kind": "schedule.display"},
        {"role": "user", "text": "\u770b\u56fe"},
    ]
