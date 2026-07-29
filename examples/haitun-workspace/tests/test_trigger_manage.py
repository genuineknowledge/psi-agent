"""Tests for the Haitun workspace ``trigger_manage`` tool."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tm: Any = importlib.import_module("trigger_manage")


@pytest.fixture()
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.anyio
async def test_create_member_added_trigger(workspace: Path) -> None:
    out = await tm.trigger_manage(
        action="create",
        trigger_name="welcome-group",
        event="feishu.chat.member_added",
        filter='{"chat_id":"oc_1"}',
        fire="tool",
        tool="feishu_message_send",
        tool_args='{"receive_id":"oc_1","text":"新人进群","receive_id_type":"chat_id"}',
        description="有人进群提醒",
    )
    assert out.startswith("Created trigger")
    assert "raw_event=" in out
    raw = (workspace / "triggers" / "welcome-group" / "TRIGGER.md").read_text(encoding="utf-8")
    assert "feishu.chat.member_added" in raw
    header, _ = tm._parse_header(raw)
    assert header["event"] == "feishu.chat.member_added"
    assert header["raw_event"] == "im.chat.member.user.added_v1"
    assert header["filter"] == {"chat_id": "oc_1"}
    assert header["fire"] == "tool"
    assert header["tool"] == "feishu_message_send"


@pytest.mark.anyio
async def test_create_allows_unknown_event_name(workspace: Path) -> None:
    """Business event registry is Channel channel_events/; Session does not gate names."""
    out = await tm.trigger_manage(
        action="create",
        trigger_name="custom-ev",
        event="feishu.custom.from_channel_events",
        fire="prompt",
    )
    assert out.startswith("Created trigger")


@pytest.mark.anyio
async def test_list_and_delete(workspace: Path) -> None:
    await tm.trigger_manage(
        action="create",
        trigger_name="t1",
        event="feishu.chat.member_added",
        fire="prompt",
    )
    listed = await tm.trigger_manage(action="list")
    assert "t1" in listed
    assert "feishu.chat.member_added" in listed
    deleted = await tm.trigger_manage(action="delete", trigger_name="t1")
    assert "Deleted" in deleted
    assert "No triggers" in await tm.trigger_manage(action="list")


def test_format_trigger_roundtrip_yaml() -> None:
    doc = tm._format_trigger_document(
        trigger_name="x",
        event="feishu.chat.member_added",
        description="d",
        content="note",
        filter={"chat_id": "oc_1"},
        fire="tool",
        tool="feishu_message_send",
        tool_args={"receive_id": "oc_1", "text": "hi"},
        raw_event="im.chat.member.user.added_v1",
    )
    header, body = tm._parse_header(doc)
    assert header["event"] == "feishu.chat.member_added"
    assert header["raw_event"] == "im.chat.member.user.added_v1"
    assert yaml.safe_load(json.dumps(header["filter"])) == {"chat_id": "oc_1"}
    assert "note" in body
