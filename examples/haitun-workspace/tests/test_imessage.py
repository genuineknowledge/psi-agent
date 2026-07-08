"""Tests for the Haitun workspace ``imessage`` tool.

``imsg`` is a macOS-only external CLI, so these tests never invoke it. They
patch ``_preflight`` (to bypass the platform/CLI check) and the subprocess
runner, then assert on argument assembly, JSON-lines parsing, and the readable
formatting — the OS-independent parts.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tool: Any = importlib.import_module("imessage")

_CHATS_JSONL = "\n".join(
    [
        "Loading chats...",  # stderr-style noise on stdout should be ignored
        json.dumps({"chat_id": 1, "display_name": "Mom", "participants": ["+14155550001"]}),
        json.dumps({"chat_id": 2, "participants": ["+14155550002", "+14155550003"]}),
    ]
)

_HISTORY_JSONL = "\n".join(
    [
        json.dumps({"date": "2026-07-08T10:00:00", "sender": "+14155550001", "text": "hi"}),
        json.dumps({"date": "2026-07-08T10:01:00", "is_from_me": True, "text": "hello back"}),
        json.dumps({"date": "2026-07-08T10:02:00", "sender": "+14155550001", "text": "pic", "attachments": [{"n": 1}]}),
    ]
)


@pytest.fixture()
def imsg(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Bypass preflight and record the argv passed to the imsg runner.

    Returns a list of the argument lists; the fake runner replies based on the
    subcommand so the formatting paths get exercised.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(tool, "_preflight", lambda: None)

    async def fake_run(args, *, timeout_seconds=60):
        calls.append(list(args))
        sub = args[0] if args else ""
        if sub == "chats":
            return 0, _CHATS_JSONL
        if sub in ("history", "search", "watch"):
            return 0, _HISTORY_JSONL
        if sub == "send":
            return 0, ""
        return 0, ""

    monkeypatch.setattr(tool, "_run", fake_run)
    return calls


def test_tool_metadata_is_loadable() -> None:
    meta = ToolFunction.from_callable(tool.imessage)
    assert meta.name == "imessage"
    props = meta.parameters["properties"]
    assert set(props) == {
        "action",
        "to",
        "text",
        "file",
        "chat_id",
        "query",
        "service",
        "limit",
        "attachments",
        "match",
    }
    assert props["limit"]["type"] == "integer"
    assert props["attachments"]["type"] == "boolean"
    # Every parameter has a default, so nothing is required.
    assert meta.parameters.get("required", []) == []


def test_preflight_blocks_off_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.sys, "platform", "linux")
    msg = asyncio.run(tool.imessage(action="chats"))
    assert msg.startswith("[Error]")
    assert "macOS" in msg


async def test_chats_assembles_args_and_parses(imsg: list[list[str]]) -> None:
    out = await tool.imessage(action="chats", limit=10)
    assert imsg[-1] == ["chats", "--limit", "10", "--json"]
    assert out.startswith("Chats:")
    assert "chat 1: Mom" in out
    assert "+14155550001" in out
    # Unnamed group falls back to the joined handles.
    assert "chat 2: +14155550002, +14155550003" in out


async def test_history_requires_chat_id(imsg: list[list[str]]) -> None:
    assert (await tool.imessage(action="history")).startswith("[Error]")


async def test_history_formats_transcript(imsg: list[list[str]]) -> None:
    out = await tool.imessage(action="history", chat_id="1", limit=5, attachments=True)
    assert imsg[-1] == ["history", "--chat-id", "1", "--limit", "5", "--attachments", "--json"]
    assert "+14155550001: hi" in out
    assert "me: hello back" in out
    assert "[attachments: 1]" in out


async def test_search_requires_query(imsg: list[list[str]]) -> None:
    assert (await tool.imessage(action="search")).startswith("[Error]")


async def test_search_assembles_args(imsg: list[list[str]]) -> None:
    await tool.imessage(action="search", query="lunch", match="exact", limit=3)
    assert imsg[-1] == ["search", "--query", "lunch", "--match", "exact", "--limit", "3", "--json"]


async def test_send_requires_recipient(imsg: list[list[str]]) -> None:
    assert (await tool.imessage(action="send", text="hi")).startswith("[Error]")


async def test_send_requires_content(imsg: list[list[str]]) -> None:
    assert (await tool.imessage(action="send", to="+14155550001")).startswith("[Error]")


async def test_send_text_default_service(imsg: list[list[str]]) -> None:
    out = await tool.imessage(action="send", to="+14155550001", text="on my way")
    # service="auto" must NOT add a --service flag (let Messages.app decide).
    assert imsg[-1] == ["send", "--to", "+14155550001", "--text", "on my way", "--json"]
    assert "sent" in out.lower()


async def test_send_with_file_and_service(imsg: list[list[str]]) -> None:
    await tool.imessage(action="send", to="Jane", text="pic", file="/tmp/a.jpg", service="imessage")
    assert imsg[-1] == [
        "send",
        "--to",
        "Jane",
        "--text",
        "pic",
        "--file",
        "/tmp/a.jpg",
        "--service",
        "imessage",
        "--json",
    ]


async def test_send_by_chat_id(imsg: list[list[str]]) -> None:
    await tool.imessage(action="send", chat_id="7", text="yo")
    assert imsg[-1] == ["send", "--chat-id", "7", "--text", "yo", "--json"]


async def test_watch_scoped_short_timeout(imsg: list[list[str]]) -> None:
    out = await tool.imessage(action="watch", chat_id="1")
    assert imsg[-1] == ["watch", "--chat-id", "1", "--json"]
    assert "+14155550001: hi" in out


async def test_unknown_action(imsg: list[list[str]]) -> None:
    assert (await tool.imessage(action="frobnicate")).startswith("[Error] Unknown action")


def test_parse_jsonl_ignores_noise() -> None:
    parsed = tool._parse_jsonl('progress line\n{"a": 1}\nnot json\n{"b": 2}\n')
    assert parsed == [{"a": 1}, {"b": 2}]
