"""Tests for the Haitun workspace ``discord_admin`` toolset.

The Discord API is never hit: ``_discord_impl._rest_client`` (which the admin
impl reuses) is monkeypatched to yield a fake REST client, so these exercise
argument validation, the missing-token config path, and result shaping without
network or hikari.
"""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

base_impl: Any = importlib.import_module("_discord_impl")
impl: Any = importlib.import_module("_discord_admin_impl")
admin: Any = importlib.import_module("discord_admin")


class _FakeGuild:
    def __init__(self, id_: int, name: str) -> None:
        self.id = id_
        self.name = name
        self.is_owner = False
        self.approximate_member_count = 3


class _FakeRole:
    def __init__(self, id_: int, name: str, position: int) -> None:
        self.id = id_
        self.name = name
        self.position = position
        self.color = 0
        self.is_hoisted = False
        self.is_mentionable = True
        self.is_managed = False
        self.permissions = 0


class _FakeChannel:
    def __init__(self, id_: int, name: str) -> None:
        self.id = id_
        self.name = name
        self.type = type("T", (), {"name": "GUILD_TEXT"})()
        self.guild_id = 42
        self.parent_id = None


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, method: str, /, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))

    async def fetch_my_guilds(self) -> list[_FakeGuild]:
        self._record("fetch_my_guilds")
        return [_FakeGuild(1, "Alpha"), _FakeGuild(2, "Beta")]

    async def fetch_roles(self, guild_id: int) -> list[_FakeRole]:
        self._record("fetch_roles", guild_id)
        return [_FakeRole(10, "everyone", 0), _FakeRole(11, "mods", 5)]

    async def create_guild_text_channel(self, guild_id: int, name: str, **kwargs: Any) -> _FakeChannel:
        self._record("create_guild_text_channel", guild_id, name, **kwargs)
        return _FakeChannel(500, name)

    async def create_guild_voice_channel(self, guild_id: int, name: str, **kwargs: Any) -> _FakeChannel:
        self._record("create_guild_voice_channel", guild_id, name, **kwargs)
        return _FakeChannel(501, name)

    async def create_guild_category(self, guild_id: int, name: str, **kwargs: Any) -> _FakeChannel:
        self._record("create_guild_category", guild_id, name, **kwargs)
        return _FakeChannel(502, name)

    async def edit_channel(self, channel_id: int, **kwargs: Any) -> _FakeChannel:
        self._record("edit_channel", channel_id, **kwargs)
        return _FakeChannel(channel_id, kwargs.get("name", "general"))

    async def delete_channel(self, channel_id: int, **kwargs: Any) -> _FakeChannel:
        self._record("delete_channel", channel_id, **kwargs)
        return _FakeChannel(channel_id, "general")

    async def add_role_to_member(self, guild_id: int, user_id: int, role_id: int, **kwargs: Any) -> None:
        self._record("add_role_to_member", guild_id, user_id, role_id, **kwargs)

    async def remove_role_from_member(self, guild_id: int, user_id: int, role_id: int, **kwargs: Any) -> None:
        self._record("remove_role_from_member", guild_id, user_id, role_id, **kwargs)

    async def edit_member(self, guild_id: int, user_id: int, **kwargs: Any) -> None:
        self._record("edit_member", guild_id, user_id, **kwargs)

    async def kick_user(self, guild_id: int, user_id: int, **kwargs: Any) -> None:
        self._record("kick_user", guild_id, user_id, **kwargs)

    async def ban_user(self, guild_id: int, user_id: int, **kwargs: Any) -> None:
        self._record("ban_user", guild_id, user_id, **kwargs)

    async def unban_user(self, guild_id: int, user_id: int, **kwargs: Any) -> None:
        self._record("unban_user", guild_id, user_id, **kwargs)


def _patch_client(monkeypatch: pytest.MonkeyPatch, error: Exception | None = None) -> _FakeClient:
    client = _FakeClient()

    @asynccontextmanager
    async def _fake_rest_client():
        if error is not None:
            raise error
        yield client

    # The admin impl aliases the base impl's _rest_client at import time, so
    # patch it on both to be safe.
    monkeypatch.setattr(base_impl, "_rest_client", _fake_rest_client)
    monkeypatch.setattr(impl, "_rest_client", _fake_rest_client)
    return client


def test_all_tools_are_loadable() -> None:
    """Every public tool must expose valid metadata for the ToolRegistry."""
    expected = {
        "list_guilds": (set(), []),
        "list_roles": ({"guild_id"}, ["guild_id"]),
        "create_channel": ({"guild_id", "name", "channel_type", "topic", "parent_id"}, ["guild_id", "name"]),
        "edit_channel": ({"channel_id", "name", "topic", "parent_id"}, ["channel_id"]),
        "delete_channel": ({"channel_id", "reason"}, ["channel_id"]),
        "grant_role": ({"guild_id", "user_id", "role_id", "reason"}, ["guild_id", "user_id", "role_id"]),
        "revoke_role": ({"guild_id", "user_id", "role_id", "reason"}, ["guild_id", "user_id", "role_id"]),
        "timeout_member": (
            {"guild_id", "user_id", "duration_minutes", "reason"},
            ["guild_id", "user_id", "duration_minutes"],
        ),
        "kick_member": ({"guild_id", "user_id", "reason"}, ["guild_id", "user_id"]),
        "ban_member": ({"guild_id", "user_id", "delete_message_days", "reason"}, ["guild_id", "user_id"]),
        "unban_member": ({"guild_id", "user_id", "reason"}, ["guild_id", "user_id"]),
    }
    for name, (props, required) in expected.items():
        meta = ToolFunction.from_callable(getattr(admin, name))
        assert meta.name == name
        assert meta.description
        assert set(meta.parameters["properties"]) == props
        assert meta.parameters["required"] == required


async def test_list_guilds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.list_guilds())
    assert result["ok"] is True
    assert [g["name"] for g in result["guilds"]] == ["Alpha", "Beta"]
    assert result["guilds"][0]["id"] == "1"


async def test_list_roles_sorted_highest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.list_roles("42"))
    assert result["ok"] is True
    assert [r["name"] for r in result["roles"]] == ["mods", "everyone"]
    assert client.calls[0] == ("fetch_roles", (42,), {})


async def test_create_channel_text_with_topic_and_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.create_channel("42", "chat", topic="hi", parent_id="900"))
    assert result["ok"] is True
    name, args, kwargs = client.calls[0]
    assert name == "create_guild_text_channel"
    assert args == (42, "chat")
    assert kwargs == {"topic": "hi", "category": 900}


async def test_create_channel_voice_ignores_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.create_channel("42", "Talk", channel_type="voice", topic="ignored"))
    assert result["ok"] is True
    name, _args, kwargs = client.calls[0]
    assert name == "create_guild_voice_channel"
    assert "topic" not in kwargs


async def test_create_category_ignores_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.create_channel("42", "Group", channel_type="category", parent_id="900"))
    assert result["ok"] is True
    name, _args, kwargs = client.calls[0]
    assert name == "create_guild_category"
    assert "category" not in kwargs


async def test_create_channel_rejects_bad_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.create_channel("42", "x", channel_type="stage"))
    assert result["ok"] is False
    assert "channel_type" in result["message"]


async def test_create_channel_rejects_empty_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.create_channel("42", "   "))
    assert result["ok"] is False
    assert "name" in result["message"]


async def test_edit_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.edit_channel("500", name="renamed", parent_id="900"))
    assert result["ok"] is True
    _name, args, kwargs = client.calls[0]
    assert args == (500,)
    assert kwargs == {"name": "renamed", "parent_category": 900}


async def test_edit_channel_requires_a_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.edit_channel("500"))
    assert result["ok"] is False
    assert "Nothing to edit" in result["message"]


async def test_delete_channel_forwards_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.delete_channel("500", reason="cleanup"))
    assert result["ok"] is True
    _name, args, kwargs = client.calls[0]
    assert args == (500,)
    assert kwargs == {"reason": "cleanup"}


async def test_delete_channel_omits_empty_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    await admin.delete_channel("500")
    assert client.calls[0][2] == {}


async def test_grant_and_revoke_role(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    granted = json.loads(await admin.grant_role("42", "7", "11"))
    assert granted["ok"] is True
    assert granted["action"] == "granted"
    assert client.calls[0] == ("add_role_to_member", (42, 7, 11), {})
    revoked = json.loads(await admin.revoke_role("42", "7", "11", reason="left team"))
    assert revoked["action"] == "revoked"
    assert client.calls[1] == ("remove_role_from_member", (42, 7, 11), {"reason": "left team"})


async def test_timeout_member_sets_future_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.timeout_member("42", "7", 30))
    assert result["ok"] is True
    assert result["timed_out_until"] is not None
    _name, _args, kwargs = client.calls[0]
    assert kwargs["communication_disabled_until"] is not None


async def test_timeout_member_zero_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.timeout_member("42", "7", 0))
    assert result["ok"] is True
    assert result["timed_out_until"] is None
    assert client.calls[0][2]["communication_disabled_until"] is None


async def test_timeout_member_rejects_over_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.timeout_member("42", "7", 28 * 24 * 60 + 1))
    assert result["ok"] is False
    assert "28 days" in result["message"]


async def test_kick_member(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.kick_member("42", "7", reason="spam"))
    assert result["ok"] is True
    assert result["action"] == "kicked"
    assert client.calls[0] == ("kick_user", (42, 7), {"reason": "spam"})


async def test_ban_member_with_message_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.ban_member("42", "7", delete_message_days=2))
    assert result["ok"] is True
    assert result["action"] == "banned"
    assert client.calls[0][2]["delete_message_seconds"] == 2 * 24 * 3600


async def test_ban_member_rejects_bad_purge_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.ban_member("42", "7", delete_message_days=9))
    assert result["ok"] is False
    assert "delete_message_days" in result["message"]


async def test_unban_member(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch)
    result = json.loads(await admin.unban_member("42", "7"))
    assert result["ok"] is True
    assert result["action"] == "unbanned"
    assert client.calls[0] == ("unban_user", (42, 7), {})


async def test_invalid_snowflake_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    result = json.loads(await admin.list_roles("not-a-number"))
    assert result["ok"] is False
    assert "numeric Discord ID" in result["message"]


async def test_missing_token_reports_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    result = json.loads(await admin.list_guilds())
    assert result["ok"] is False
    assert "DISCORD_BOT_TOKEN" in result["message"]


class _FakeHTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def test_api_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, error=_FakeHTTPError(403, "missing permissions"))
    result = json.loads(await admin.kick_member("42", "7"))
    assert result["ok"] is False
    assert "403" in result["message"]
