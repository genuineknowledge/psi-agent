"""Private helper for the ``discord_admin`` toolset.

Manages a Discord server over the REST API — no gateway, no persistent bot
loop — so each tool call is a self-contained request/response. It reuses the
stateless ``RESTApp`` machinery from ``_discord_impl`` (auth, per-call client
lifecycle, snowflake parsing, error shaping) and adds the guild/channel/role
and moderation operations on top.

Auth: a bot token read from ``DISCORD_BOT_TOKEN`` (see ``_discord_impl``). The
bot must be invited with the ``bot`` scope and hold the permissions each action
needs — ``MANAGE_CHANNELS`` to create/edit/delete channels, ``MANAGE_ROLES`` to
grant/revoke roles, ``MODERATE_MEMBERS`` to time members out, ``KICK_MEMBERS``
to kick, and ``BAN_MEMBERS`` to ban/unban. As always its role must sit above
the target member/role in the hierarchy. Every helper returns a plain ``dict``
— ``ok=True`` with data, or ``ok=False`` with a ``message`` — so the thin tool
layer never has to handle exceptions.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from os.path import dirname

_HERE = dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import datetime
from typing import Any

import _discord_impl as _base

# Reuse the base toolset's request plumbing so both toolsets behave identically.
_rest_client = _base._rest_client
_snowflake = _base._snowflake
_error = _base._error
_describe_exception = _base._describe_exception
_channel_dict = _base._channel_dict
_ConfigError = _base._ConfigError

_MAX_TIMEOUT_DAYS = 28  # Discord hard-caps a communication timeout at 28 days.


def _reason_kwargs(reason: str) -> dict[str, Any]:
    """Only forward ``reason`` when set, so hikari's UNDEFINED default stands in."""
    reason = reason.strip()
    return {"reason": reason} if reason else {}


def _guild_dict(guild: Any) -> dict[str, Any]:
    return {
        "id": str(guild.id),
        "name": getattr(guild, "name", None),
        "is_owner": bool(getattr(guild, "is_owner", False)),
        "approximate_member_count": getattr(guild, "approximate_member_count", None),
    }


def _role_dict(role: Any) -> dict[str, Any]:
    color = getattr(role, "color", None)
    return {
        "id": str(role.id),
        "name": getattr(role, "name", None),
        "position": getattr(role, "position", None),
        "color": int(color) if color is not None else None,
        "is_hoisted": bool(getattr(role, "is_hoisted", False)),
        "is_mentionable": bool(getattr(role, "is_mentionable", False)),
        "is_managed": bool(getattr(role, "is_managed", False)),
        "permissions": str(int(getattr(role, "permissions", 0) or 0)),
    }


_CHANNEL_FACTORIES = {
    "text": "create_guild_text_channel",
    "voice": "create_guild_voice_channel",
    "category": "create_guild_category",
}


async def list_guilds_impl() -> dict[str, Any]:
    """List the guilds the bot is a member of."""
    try:
        async with _rest_client() as client:
            guilds = await client.fetch_my_guilds()
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guilds": [_guild_dict(g) for g in guilds]}


async def list_roles_impl(guild_id: str) -> dict[str, Any]:
    """List every role in a guild (highest position first)."""
    try:
        gid = _snowflake(guild_id, "guild_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            roles = await client.fetch_roles(gid)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    ordered = sorted(roles, key=lambda r: getattr(r, "position", 0), reverse=True)
    return {"ok": True, "guild_id": str(gid), "roles": [_role_dict(r) for r in ordered]}


async def create_channel_impl(
    guild_id: str, name: str, channel_type: str = "text", topic: str = "", parent_id: str = ""
) -> dict[str, Any]:
    """Create a text/voice/category channel in a guild."""
    name = name.strip()
    if not name:
        return _error("`name` must not be empty.")
    kind = channel_type.strip().lower()
    factory = _CHANNEL_FACTORIES.get(kind)
    if factory is None:
        return _error(f"`channel_type` must be one of {sorted(_CHANNEL_FACTORIES)}, got {channel_type!r}.")
    try:
        gid = _snowflake(guild_id, "guild_id")
        kwargs: dict[str, Any] = {}
        if topic.strip() and kind == "text":
            kwargs["topic"] = topic.strip()
        if parent_id.strip() and kind != "category":
            kwargs["category"] = _snowflake(parent_id, "parent_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            channel = await getattr(client, factory)(gid, name, **kwargs)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "channel": _channel_dict(channel)}


async def edit_channel_impl(channel_id: str, name: str = "", topic: str = "", parent_id: str = "") -> dict[str, Any]:
    """Rename a channel and/or change its topic or parent category."""
    try:
        cid = _snowflake(channel_id, "channel_id")
        kwargs: dict[str, Any] = {}
        if name.strip():
            kwargs["name"] = name.strip()
        if topic.strip():
            kwargs["topic"] = topic.strip()
        if parent_id.strip():
            kwargs["parent_category"] = _snowflake(parent_id, "parent_id")
    except ValueError as exc:
        return _error(str(exc))
    if not kwargs:
        return _error("Nothing to edit: pass at least one of `name`, `topic`, or `parent_id`.")
    try:
        async with _rest_client() as client:
            channel = await client.edit_channel(cid, **kwargs)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "channel": _channel_dict(channel)}


async def delete_channel_impl(channel_id: str, reason: str = "") -> dict[str, Any]:
    """Delete a channel by id."""
    try:
        cid = _snowflake(channel_id, "channel_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            channel = await client.delete_channel(cid, **_reason_kwargs(reason))
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "channel": _channel_dict(channel)}


async def grant_role_impl(guild_id: str, user_id: str, role_id: str, reason: str = "") -> dict[str, Any]:
    """Add a role to a guild member."""
    try:
        gid = _snowflake(guild_id, "guild_id")
        uid = _snowflake(user_id, "user_id")
        rid = _snowflake(role_id, "role_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            await client.add_role_to_member(gid, uid, rid, **_reason_kwargs(reason))
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "user_id": str(uid), "role_id": str(rid), "action": "granted"}


async def revoke_role_impl(guild_id: str, user_id: str, role_id: str, reason: str = "") -> dict[str, Any]:
    """Remove a role from a guild member."""
    try:
        gid = _snowflake(guild_id, "guild_id")
        uid = _snowflake(user_id, "user_id")
        rid = _snowflake(role_id, "role_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            await client.remove_role_from_member(gid, uid, rid, **_reason_kwargs(reason))
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "user_id": str(uid), "role_id": str(rid), "action": "revoked"}


async def timeout_member_impl(guild_id: str, user_id: str, duration_minutes: int, reason: str = "") -> dict[str, Any]:
    """Time a member out for ``duration_minutes`` (0 clears the timeout)."""
    try:
        gid = _snowflake(guild_id, "guild_id")
        uid = _snowflake(user_id, "user_id")
    except ValueError as exc:
        return _error(str(exc))
    if duration_minutes < 0:
        return _error("`duration_minutes` must be >= 0 (use 0 to clear a timeout).")
    max_minutes = _MAX_TIMEOUT_DAYS * 24 * 60
    if duration_minutes > max_minutes:
        return _error(f"`duration_minutes` must be <= {max_minutes} ({_MAX_TIMEOUT_DAYS} days), Discord's cap.")
    if duration_minutes == 0:
        until = None
    else:
        until = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(minutes=duration_minutes)
    try:
        async with _rest_client() as client:
            await client.edit_member(gid, uid, communication_disabled_until=until, **_reason_kwargs(reason))
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {
        "ok": True,
        "guild_id": str(gid),
        "user_id": str(uid),
        "timed_out_until": until.isoformat() if until else None,
    }


async def kick_member_impl(guild_id: str, user_id: str, reason: str = "") -> dict[str, Any]:
    """Kick a member from a guild."""
    try:
        gid = _snowflake(guild_id, "guild_id")
        uid = _snowflake(user_id, "user_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            await client.kick_user(gid, uid, **_reason_kwargs(reason))
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "user_id": str(uid), "action": "kicked"}


async def ban_member_impl(
    guild_id: str, user_id: str, delete_message_days: int = 0, reason: str = ""
) -> dict[str, Any]:
    """Ban a user, optionally purging their recent messages."""
    try:
        gid = _snowflake(guild_id, "guild_id")
        uid = _snowflake(user_id, "user_id")
    except ValueError as exc:
        return _error(str(exc))
    if not 0 <= delete_message_days <= 7:
        return _error("`delete_message_days` must be between 0 and 7.")
    kwargs = _reason_kwargs(reason)
    if delete_message_days:
        kwargs["delete_message_seconds"] = delete_message_days * 24 * 3600
    try:
        async with _rest_client() as client:
            await client.ban_user(gid, uid, **kwargs)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "user_id": str(uid), "action": "banned"}


async def unban_member_impl(guild_id: str, user_id: str, reason: str = "") -> dict[str, Any]:
    """Lift a ban on a user."""
    try:
        gid = _snowflake(guild_id, "guild_id")
        uid = _snowflake(user_id, "user_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            await client.unban_user(gid, uid, **_reason_kwargs(reason))
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "user_id": str(uid), "action": "unbanned"}
