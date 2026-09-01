"""Tests for the Haitun workspace ``trigger_manage`` tool."""

from __future__ import annotations

import importlib
import importlib.util
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


def test_assignment_delivery_trigger_uses_silent_tool_fire() -> None:
    trigger_path = WORKSPACE_ROOT / "triggers" / "assignment-delivery-refresh" / "TRIGGER.md"
    raw = trigger_path.read_text(encoding="utf-8")
    header, _body = tm._parse_header(raw)

    assert header["event"] == "haitun.assignment.delivery_check"
    assert header["source"] == "haitun"
    assert header["fire"] == "tool"
    assert header["tool"] == "assignment_delivery_refresh"
    assert header["visibility"] == "silent"


@pytest.mark.anyio
async def test_assignment_delivery_event_routes_each_registered_feishu_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text(
        json.dumps(
            {
                "ou_b": {"token": "token-b"},
                "ou_a": {"token": "token-a"},
                "invalid": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUSION_MEMORY_TOKEN_MAP_FILE", str(token_map))
    producer_path = WORKSPACE_ROOT / "channel_events" / "feishu" / "assignment_delivery_check" / "produce.py"
    spec = importlib.util.spec_from_file_location("assignment_delivery_check_producer", producer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert await module._registered_open_ids() == ["ou_a", "ou_b"]


@pytest.mark.anyio
async def test_assignment_delivery_event_isolates_one_user_emit_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_map = tmp_path / "tokens.json"
    token_map.write_text(
        json.dumps(
            {
                "ou_a": {"token": "token-a"},
                "ou_b": {"token": "token-b"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FUSION_MEMORY_TOKEN_MAP_FILE", str(token_map))
    producer_path = WORKSPACE_ROOT / "channel_events" / "feishu" / "assignment_delivery_check" / "produce.py"
    spec = importlib.util.spec_from_file_location("assignment_delivery_check_isolation", producer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    emitted: list[str] = []

    class StopProducer(BaseException):
        pass

    class FakeContext:
        async def emit(self, envelope: dict[str, object]) -> None:
            routing = envelope.get("routing")
            assert isinstance(routing, dict)
            open_id = routing.get("open_id")
            assert isinstance(open_id, str)
            emitted.append(open_id)
            if open_id == "ou_a":
                raise RuntimeError("one Session is unavailable")

    async def stop_after_one_iteration(_seconds: float) -> None:
        raise StopProducer

    monkeypatch.setattr(module.anyio, "sleep", stop_after_one_iteration)

    with pytest.raises(StopProducer):
        await module.produce(FakeContext())

    assert emitted == ["ou_a", "ou_b"]


@pytest.mark.anyio
async def test_assignment_delivery_event_survives_token_map_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_path = WORKSPACE_ROOT / "channel_events" / "feishu" / "assignment_delivery_check" / "produce.py"
    spec = importlib.util.spec_from_file_location("assignment_delivery_check_read_failure", producer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class StopProducer(BaseException):
        pass

    async def broken_registered_open_ids() -> list[str]:
        raise UnicodeError("token map changed during read")

    async def stop_after_recovery(_seconds: float) -> None:
        raise StopProducer

    monkeypatch.setattr(module, "_registered_open_ids", broken_registered_open_ids)
    monkeypatch.setattr(module.anyio, "sleep", stop_after_recovery)

    with pytest.raises(StopProducer):
        await module.produce(object())
