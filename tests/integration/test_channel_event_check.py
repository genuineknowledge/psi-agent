"""Tests for the ``channel_event_check`` agent-package self-check tool.

Covers the three gaps it exists to close: naming the loaded events, exposing the
real field layout of a platform event, and dry-running a mapper so an empty
result is diagnosable instead of silent.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

HAITUN = Path(__file__).parents[2] / "agents" / "feishu"

_BROKEN_MAP = """
def map_event(raw):
    event = raw.get("event") or {}
    # The classic defect: these live at event.message.*, not at event top level.
    message_id = str(event.get("message_id") or "").strip()
    chat_id = str(event.get("chat_id") or "").strip()
    if not message_id or not chat_id:
        return []
    return [{"payload": {"chat_id": chat_id}}]
"""

_FIXED_MAP = """
def map_event(raw):
    message = (raw.get("event") or {}).get("message") or {}
    chat_id = str(message.get("chat_id") or "").strip()
    if not chat_id:
        return []
    return [{"payload": {"chat_id": chat_id}}]
"""


def _load_tool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(HAITUN / "tools"))
    sys.modules.pop("channel_event_check", None)
    return importlib.import_module("channel_event_check")


def _write_event(root: Path, map_source: str) -> None:
    event_dir = root / "channel_events" / "feishu" / "chat_message_received"
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "EVENT.yaml").write_text(
        "name: feishu.chat.message_received\nsource: feishu\nkind: platform_map\n"
        "platform_event: im.message.receive_v1\ndescription: probe fixture\n",
        encoding="utf-8",
    )
    (event_dir / "map.py").write_text(map_source, encoding="utf-8")


@pytest.mark.anyio
async def test_shape_locates_fields_inside_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of ``shape``: chat_id is nested, and the tool says where."""
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="shape", platform_event="im.message.receive_v1")
    assert "real SDK model P2ImMessageReceiveV1" in out
    assert "event['message']['chat_id']" in out
    assert "event['sender']['sender_id']['open_id']" in out
    # And it must not claim a top-level chat_id exists.
    assert "event['chat_id']" not in out


@pytest.mark.anyio
async def test_shape_reports_the_delivery_id_for_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="shape", platform_event="im.chat.member.user.added_v1")
    assert "header" in out
    assert "event_id" in out
    assert "event['users'][0]['user_id']['open_id']" in out


@pytest.mark.anyio
async def test_shape_rejects_unknown_platform_event(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="shape", platform_event="im.not.a.real.event")
    assert out.startswith("[Error]")
    assert "im.message.receive_v1" in out  # lists what it does know


@pytest.mark.anyio
async def test_list_names_loaded_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(HAITUN))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="list")
    assert "feishu.chat.member_added" in out
    assert "im.chat.member.user.added_v1" in out


@pytest.mark.anyio
async def test_probe_reports_ok_for_the_bundled_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(HAITUN))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.member_added")
    assert "Result: OK" in out
    assert "ou_sample_newcomer" in out
    assert "unique per delivery" in out


@pytest.mark.anyio
async def test_probe_diagnoses_a_wrong_field_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap this closes: [] used to be indistinguishable from a deduped event."""
    _write_event(tmp_path, _BROKEN_MAP)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.message_received")
    assert "Result: EMPTY" in out
    # It must hand over the paths that would have worked.
    assert "event['message']['chat_id']" in out
    assert "event['message']['message_id']" in out


@pytest.mark.anyio
async def test_probe_confirms_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_event(tmp_path, _FIXED_MAP)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.message_received")
    assert "Result: OK" in out
    assert "oc_sample_chat" in out


@pytest.mark.anyio
async def test_probe_surfaces_a_raising_mapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_event(tmp_path, "def map_event(raw):\n    return raw['event']['chat_id']\n")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.message_received")
    assert "map_event RAISED" in out
    assert "KeyError" in out


@pytest.mark.anyio
async def test_probe_flags_a_non_list_return(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_event(tmp_path, "def map_event(raw):\n    return {'payload': {}}\n")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.message_received")
    assert "WRONG RETURN TYPE" in out


@pytest.mark.anyio
async def test_probe_warns_when_key_does_not_vary_per_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A key without a per-delivery part dedupes away every later occurrence."""
    _write_event(
        tmp_path,
        "def map_event(raw):\n"
        "    chat_id = ((raw.get('event') or {}).get('message') or {}).get('chat_id')\n"
        "    return [{'payload': {'chat_id': chat_id}, 'idempotency_key': 'fixed:' + chat_id}]\n",
    )
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.message_received")
    assert "Result: OK" in out
    assert "does NOT vary per delivery" in out


@pytest.mark.anyio
async def test_probe_rejects_unknown_event_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(HAITUN))
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="probe", event="feishu.chat.nope")
    assert out.startswith("[Error]")
    assert "feishu.chat.member_added" in out  # lists the real ones


@pytest.mark.anyio
async def test_unknown_action_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _load_tool(monkeypatch)
    out = await tool.channel_event_check(action="explode")
    assert out.startswith("[Error]")


@pytest.mark.anyio
async def test_probe_never_mutates_the_sample_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Samples are deep-copied — one probe must not poison the next."""
    monkeypatch.setenv("WORKSPACE_DIR", str(HAITUN))
    tool = _load_tool(monkeypatch)
    first, _ = tool._build_sample("im.chat.member.user.added_v1", {"chat_id": "oc_mutated"})
    second, _ = tool._build_sample("im.chat.member.user.added_v1")
    assert tool._raw_to_dict(first)["event"]["chat_id"] == "oc_mutated"
    assert tool._raw_to_dict(second)["event"]["chat_id"] == "oc_sample_chat"
