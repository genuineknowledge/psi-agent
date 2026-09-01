"""Tests for the Haitun workspace ``discord`` toolset.

The Discord API is never hit: ``_discord_impl._rest_client`` is monkeypatched
to yield a fake REST client, so these exercise argument validation, the
missing-token config path, and result shaping without network or hikari.
"""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_discord_impl")
discord_tool: Any = importlib.import_module("discord")


class _FakeMember:
    def __init__(self, id_: int, username: str, nickname: str | None = None, is_bot: bool = False) -> None:
        self.id = id_
        self.username = username
        self.display_name = nickname or username
        self.nickname = nickname
        self.is_bot = is_bot


class _FakeAuthor:
    def __init__(self, id_: int, username: str) -> None:
        self.id = id_
        self.username = username


class _FakeMessage:
    def __init__(self, id_: int, channel_id: int, content: str) -> None:
        self.id = id_
        self.channel_id = channel_id
        self.author = _FakeAuthor(1, "author")
        self.content = content
        self.created_at = datetime(2026, 7, 10, tzinfo=UTC)
        self.attachments = []


class _FakeChannel:
    def __init__(self, id_: int, name: str) -> None:
        self.id = id_
        self.name = name
        self.type = type("T", (), {"name": "GUILD_TEXT"})()
        self.guild_id = 42
        self.parent_id = None


class _FakeLazyIterator:
    """Mimics hikari's LazyIterator: ``await client.fetch_messages(id).limit(n)``."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def limit(self, n: int) -> _FakeLazyIterator:
        return _FakeLazyIterator(self._items[:n])

    def __await__(self):
        async def _resolve() -> list[Any]:
            return self._items

        return _resolve().__await__()


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def search_members(self, guild_id: int, query: str) -> list[_FakeMember]:
        self.calls.append(("search_members", (guild_id, query)))
        return [_FakeMember(100, "alice"), _FakeMember(101, "alicia", "Ali")]

    def fetch_messages(self, channel_id: int) -> _FakeLazyIterator:
        self.calls.append(("fetch_messages", (channel_id,)))
        return _FakeLazyIterator([_FakeMessage(200, channel_id, "hi"), _FakeMessage(201, channel_id, "there")])

    async def create_message(self, channel_id: int, content: str) -> _FakeMessage:
        self.calls.append(("create_message", (channel_id, content)))
        return _FakeMessage(300, channel_id, content)

    async def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        self.calls.append(("add_reaction", (channel_id, message_id, emoji)))

    async def fetch_channel(self, channel_id: int) -> _FakeChannel:
        self.calls.append(("fetch_channel", (channel_id,)))
        return _FakeChannel(channel_id, "general")

    async def fetch_guild_channels(self, guild_id: int) -> list[_FakeChannel]:
        self.calls.append(("fetch_guild_channels", (guild_id,)))
        return [_FakeChannel(500, "general"), _FakeChannel(501, "random")]


def _patch_client(monkeypatch: pytest.MonkeyPatch, error: Exception | None = None) -> _FakeClient:
    client = _FakeClient()

    @asynccontextmanager
    async def _fake_rest_client():
        if error is not None:
            raise error
        yield client

    monkeypatch.setattr(impl, "_rest_client", _fake_rest_client)
    return client


def test_all_tools_are_loadable() -> None:
    """Every public tool must expose valid metadata for the ToolRegistry."""
    expected = {
        "search_members": ({"guild_id", "query", "limit"}, ["guild_id", "query"]),
        "fetch_messages": ({"channel_id", "limit"}, ["channel_id"]),
        "send_message": ({"channel_id", "content"}, ["channel_id", "content"]),
        "react": ({"channel_id", "message_id", "emoji"}, ["channel_id", "message_id", "emoji"]),
        "fetch_channel": ({"channel_id"}, ["channel_id"]),
        "list_channels": ({"guild_id"}, ["guild_id"]),
    }
    for name, (props, required) in expected.items():
        meta = ToolFunction.from_callable(getattr(discord_tool, name))
        assert meta.name == name
        assert meta.description
        assert set(meta.parameters["properties"]) == props
        assert meta.parameters["required"] == required


async def test_search_members(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await discord_tool.search_members("42", "ali"))
    assert result["ok"] is True
    assert [m["username"] for m in result["members"]] == ["alice", "alicia"]
    assert client.calls[0] == ("search_members", (42, "ali"))


async def test_search_members_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await discord_tool.search_members("42", "ali", limit=1))
    assert len(result["members"]) == 1


async def test_fetch_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await discord_tool.fetch_messages("77", limit=5))
    assert result["ok"] is True
    assert result["channel_id"] == "77"
    assert [m["content"] for m in result["messages"]] == ["hi", "there"]
    assert result["messages"][0]["author"] == {"id": "1", "username": "author"}
    assert client.calls[0] == ("fetch_messages", (77,))


async def test_send_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await discord_tool.send_message("77", "hello world"))
    assert result["ok"] is True
    assert result["message"]["content"] == "hello world"
    assert client.calls[0] == ("create_message", (77, "hello world"))


async def test_send_message_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await discord_tool.send_message("77", "   "))
    assert result["ok"] is False
    assert "content" in result["message"]


async def test_react(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await discord_tool.react("77", "200", "👍"))
    assert result["ok"] is True
    assert result["emoji"] == "👍"
    assert client.calls[0] == ("add_reaction", (77, 200, "👍"))


async def test_fetch_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await discord_tool.fetch_channel("500"))
    assert result["ok"] is True
    assert result["channel"] == {
        "id": "500",
        "name": "general",
        "type": "GUILD_TEXT",
        "guild_id": "42",
        "parent_id": None,
    }


async def test_list_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await discord_tool.list_channels("42"))
    assert result["ok"] is True
    assert [c["name"] for c in result["channels"]] == ["general", "random"]


async def test_invalid_snowflake_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await discord_tool.fetch_channel("not-a-number"))
    assert result["ok"] is False
    assert "numeric Discord ID" in result["message"]


async def test_missing_token_reports_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the real _rest_client with no token set: it must surface a
    # config error instead of raising.
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    result = json.loads(await discord_tool.list_channels("42"))
    assert result["ok"] is False
    assert "DISCORD_BOT_TOKEN" in result["message"]


class _FakeHTTPError(Exception):
    """Stands in for a hikari HTTP error, which carries a ``status`` attribute."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def test_api_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, error=_FakeHTTPError(429, "rate limited"))
    result = json.loads(await discord_tool.list_channels("42"))
    assert result["ok"] is False
    assert "429" in result["message"]
