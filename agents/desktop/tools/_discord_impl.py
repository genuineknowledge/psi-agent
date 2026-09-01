"""Private helper for the ``discord`` toolset.

Talks to the Discord HTTP API over REST only — no gateway, no persistent bot
loop — so each tool call is a self-contained request/response. It uses
`hikari <https://pypi.org/project/hikari/>`_'s ``RESTApp``, which handles auth,
rate-limit backoff, and JSON (de)serialization for us.

Auth: a bot token read from the ``DISCORD_BOT_TOKEN`` environment variable
(load it into the workspace ``.env`` / process env before starting the agent).
The bot must be invited to the target guild with the relevant intents/scopes
(``bot`` scope, plus the ``GUILD_MEMBERS`` privileged intent for
``search_members``). Every helper returns a plain ``dict`` — ``ok=True`` with
data, or ``ok=False`` with a ``message`` — so the thin tool layer never has to
handle exceptions.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TOKEN_ENV = "DISCORD_BOT_TOKEN"
# Cap how many messages/members/channels we ever pull in one call so a busy
# server can't blow up the context window or hang the turn.
_MAX_LIMIT = 100


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _clamp(limit: int, default: int) -> int:
    if limit <= 0:
        return default
    return min(limit, _MAX_LIMIT)


@asynccontextmanager
async def _rest_client() -> AsyncIterator[Any]:
    """Yield an authenticated hikari REST client, or raise ``_ConfigError``.

    A fresh ``RESTApp`` is spun up per call and torn down afterwards; that keeps
    the tools stateless (no shared event-loop-bound client to leak across
    calls) at the cost of a short connection setup each time — fine for the
    interactive, low-frequency way the agent uses these.
    """
    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        raise _ConfigError(
            f"Discord is not configured. Set the {_TOKEN_ENV} environment variable to a bot token "
            "(the bot must be invited to the server; search_members also needs the GUILD_MEMBERS intent)."
        )

    import hikari  # noqa: PLC0415 — lazy so the tool file still loads when hikari is absent

    app = hikari.RESTApp()
    await app.start()
    try:
        async with app.acquire(token, token_type=hikari.TokenType.BOT) as client:
            yield client
    finally:
        await app.close()


class _ConfigError(RuntimeError):
    """Raised when the bot token is missing; turned into an ``ok=False`` dict."""


def _snowflake(value: str, field: str) -> int:
    """Discord IDs are 64-bit ints; the LLM passes them as strings. Parse safely."""
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"`{field}` must be a numeric Discord ID, got {value!r}.")
    return int(text)


def _describe_exception(exc: Exception) -> str:
    """Turn a hikari HTTP error into a short, actionable message."""
    status = getattr(exc, "status", None)
    detail = str(exc).strip() or exc.__class__.__name__
    if status is not None:
        return f"Discord API error (HTTP {status}): {detail}"
    return f"Discord request failed: {exc.__class__.__name__}: {detail}"


def _member_dict(member: Any) -> dict[str, Any]:
    return {
        "id": str(member.id),
        "username": member.username,
        "display_name": getattr(member, "display_name", None) or member.username,
        "nickname": getattr(member, "nickname", None),
        "is_bot": bool(getattr(member, "is_bot", False)),
    }


def _message_dict(message: Any) -> dict[str, Any]:
    author = getattr(message, "author", None)
    return {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "author": None if author is None else {"id": str(author.id), "username": author.username},
        "content": message.content or "",
        "timestamp": message.created_at.isoformat() if getattr(message, "created_at", None) else None,
        "attachments": [str(a.url) for a in getattr(message, "attachments", []) or []],
    }


def _channel_dict(channel: Any) -> dict[str, Any]:
    channel_type = getattr(channel, "type", None)
    return {
        "id": str(channel.id),
        "name": getattr(channel, "name", None),
        "type": getattr(channel_type, "name", str(channel_type)) if channel_type is not None else None,
        "guild_id": str(channel.guild_id) if getattr(channel, "guild_id", None) else None,
        "parent_id": str(channel.parent_id) if getattr(channel, "parent_id", None) else None,
    }


async def search_members_impl(guild_id: str, query: str, limit: int = 25) -> dict[str, Any]:
    """Search a guild's members whose username/nickname starts with ``query``."""
    query = query.strip()
    if not query:
        return _error("`query` must not be empty.")
    try:
        gid = _snowflake(guild_id, "guild_id")
    except ValueError as exc:
        return _error(str(exc))
    n = _clamp(limit, 25)
    try:
        async with _rest_client() as client:
            members = await client.search_members(gid, query)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "query": query, "members": [_member_dict(m) for m in members[:n]]}


async def fetch_messages_impl(channel_id: str, limit: int = 20) -> dict[str, Any]:
    """Fetch the most recent messages from a channel (newest first)."""
    try:
        cid = _snowflake(channel_id, "channel_id")
    except ValueError as exc:
        return _error(str(exc))
    n = _clamp(limit, 20)
    try:
        async with _rest_client() as client:
            # fetch_messages returns a LazyIterator, newest-first; .limit caps the pull.
            messages = await client.fetch_messages(cid).limit(n)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "channel_id": str(cid), "messages": [_message_dict(m) for m in messages]}


async def send_message_impl(channel_id: str, content: str) -> dict[str, Any]:
    """Send a text message to a channel."""
    content = content.strip()
    if not content:
        return _error("`content` must not be empty.")
    try:
        cid = _snowflake(channel_id, "channel_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            message = await client.create_message(cid, content=content)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "message": _message_dict(message)}


async def react_impl(channel_id: str, message_id: str, emoji: str) -> dict[str, Any]:
    """Add a reaction emoji to a message."""
    emoji = emoji.strip()
    if not emoji:
        return _error("`emoji` must not be empty.")
    try:
        cid = _snowflake(channel_id, "channel_id")
        mid = _snowflake(message_id, "message_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            await client.add_reaction(cid, mid, emoji)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "channel_id": str(cid), "message_id": str(mid), "emoji": emoji}


async def fetch_channel_impl(channel_id: str) -> dict[str, Any]:
    """Fetch metadata for a single channel by id."""
    try:
        cid = _snowflake(channel_id, "channel_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            channel = await client.fetch_channel(cid)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "channel": _channel_dict(channel)}


async def list_channels_impl(guild_id: str) -> dict[str, Any]:
    """List every channel in a guild."""
    try:
        gid = _snowflake(guild_id, "guild_id")
    except ValueError as exc:
        return _error(str(exc))
    try:
        async with _rest_client() as client:
            channels = await client.fetch_guild_channels(gid)
    except _ConfigError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(_describe_exception(exc))
    return {"ok": True, "guild_id": str(gid), "channels": [_channel_dict(c) for c in channels]}
