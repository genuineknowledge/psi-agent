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
async def test_history_hides_leftover_heartbeat_turns(tmp_path: str) -> None:
    """Heartbeat turns from a now-removed schedule must not surface on reload."""
    hm = HistoryManager()
    hist_dir = anyio.Path(str(tmp_path)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "user", "content": "real question"}',
            '{"role": "assistant", "content": "real answer"}',
            '{"role": "user", "content": "# Heartbeat Task\\n\\nRespond with HEARTBEAT_OK."}',
            '{"role": "assistant", "content": "HEARTBEAT_OK"}',
            '{"role": "assistant", "content": "**HEARTBEAT_OK**"}',
            '{"role": "assistant", "content": "HEARTBEAT_OK."}',
            '{"role": "user", "content": "another one"}',
        ]
    )
    await (hist_dir / "s2.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path), "s2")

    assert result == [
        {"role": "user", "text": "real question"},
        {"role": "assistant", "text": "real answer"},
        {"role": "user", "text": "another one"},
    ]


@pytest.mark.anyio
async def test_history_keeps_messages_that_merely_mention_heartbeat(tmp_path: str) -> None:
    """A genuine message that talks about HEARTBEAT_OK must not be filtered."""
    hm = HistoryManager()
    hist_dir = anyio.Path(str(tmp_path)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "user", "content": "what does HEARTBEAT_OK mean?"}',
            '{"role": "assistant", "content": "It signals the agent replied HEARTBEAT_OK."}',
        ]
    )
    await (hist_dir / "s3.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path), "s3")

    assert result == [
        {"role": "user", "text": "what does HEARTBEAT_OK mean?"},
        {"role": "assistant", "text": "It signals the agent replied HEARTBEAT_OK."},
    ]
