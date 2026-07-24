"""Discord admin toolset — manage a Discord server over the REST API.

Exposes one tool per administrative action so the agent can run a server it has
been given staff permissions on: inspect structure (``list_guilds``,
``list_roles``), shape channels (``create_channel``, ``edit_channel``,
``delete_channel``), assign roles (``grant_role``, ``revoke_role``), and
moderate members (``timeout_member``, ``kick_member``, ``ban_member``,
``unban_member``). Each is a thin async wrapper over the Discord HTTP API (via
hikari's REST-only client); the real logic lives in ``_discord_admin_impl`` so
the heavy third-party import stays out of the tool-discovery path.

Auth: set ``DISCORD_BOT_TOKEN`` in the environment to a bot token, and invite
the bot with the permissions each action needs (MANAGE_CHANNELS, MANAGE_ROLES,
MODERATE_MEMBERS, KICK_MEMBERS, BAN_MEMBERS). The bot's own role must sit above
any role or member it acts on. IDs (guild/channel/user/role) are Discord
snowflakes — pass them as strings.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from os.path import dirname

_HERE = dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import json

import _discord_admin_impl as _d


def _dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


async def list_guilds() -> str:
    """List the Discord servers (guilds) the bot belongs to.

    Enumerates every guild the bot is a member of so you can discover guild IDs
    to manage. Use it first when you don't yet know the server's ID.

    Returns:
        JSON with ok=true and a ``guilds`` list ({id, name, is_owner,
        approximate_member_count}), or ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.list_guilds_impl())


async def list_roles(guild_id: str) -> str:
    """List every role in a Discord server, highest first.

    Enumerates the roles in ``guild_id`` (ordered by position, top-down) so you
    can resolve a role ID before granting or revoking it, and see the hierarchy.

    Args:
        guild_id: The server (guild) ID whose roles to list, as a string of digits.

    Returns:
        JSON with ok=true and a ``roles`` list ({id, name, position, color,
        is_hoisted, is_mentionable, is_managed, permissions}), or ok=false with
        a ``message`` on failure.
    """
    return _dumps(await _d.list_roles_impl(guild_id=guild_id))


async def create_channel(
    guild_id: str, name: str, channel_type: str = "text", topic: str = "", parent_id: str = ""
) -> str:
    """Create a channel in a Discord server.

    Adds a new ``text``, ``voice``, or ``category`` channel named ``name`` to
    ``guild_id``. Text channels may take a ``topic``; text and voice channels
    may be nested under a category via ``parent_id``. Requires the bot to have
    the MANAGE_CHANNELS permission.

    Args:
        guild_id: The server (guild) ID to create the channel in, as a string of digits.
        name: The channel name (2-100 characters for text/voice).
        channel_type: One of "text", "voice", or "category" (default "text").
        topic: Optional channel topic; only applied to text channels.
        parent_id: Optional category channel ID to nest this channel under;
            ignored when creating a category.

    Returns:
        JSON with ok=true and the created ``channel`` object ({id, name, type,
        guild_id, parent_id}), or ok=false with a ``message`` on failure.
    """
    return _dumps(
        await _d.create_channel_impl(
            guild_id=guild_id, name=name, channel_type=channel_type, topic=topic, parent_id=parent_id
        )
    )


async def edit_channel(channel_id: str, name: str = "", topic: str = "", parent_id: str = "") -> str:
    """Edit a Discord channel's name, topic, or parent category.

    Updates whichever of ``name``, ``topic``, or ``parent_id`` you provide on
    ``channel_id`` and leaves the rest unchanged. Pass at least one field.
    Requires the bot to have the MANAGE_CHANNELS permission.

    Args:
        channel_id: The channel ID to edit, as a string of digits.
        name: New channel name, if renaming.
        topic: New channel topic, if changing (text channels).
        parent_id: New parent category channel ID, to move the channel.

    Returns:
        JSON with ok=true and the updated ``channel`` object, or ok=false with a
        ``message`` on failure.
    """
    return _dumps(await _d.edit_channel_impl(channel_id=channel_id, name=name, topic=topic, parent_id=parent_id))


async def delete_channel(channel_id: str, reason: str = "") -> str:
    """Delete a Discord channel by its ID.

    Permanently removes ``channel_id`` from its server. This cannot be undone.
    Requires the bot to have the MANAGE_CHANNELS permission.

    Args:
        channel_id: The channel ID to delete, as a string of digits.
        reason: Optional audit-log reason for the deletion.

    Returns:
        JSON with ok=true and the deleted ``channel`` object, or ok=false with a
        ``message`` on failure.
    """
    return _dumps(await _d.delete_channel_impl(channel_id=channel_id, reason=reason))


async def grant_role(guild_id: str, user_id: str, role_id: str, reason: str = "") -> str:
    """Grant a role to a member of a Discord server.

    Adds role ``role_id`` to member ``user_id`` in ``guild_id``. Requires the
    bot to have MANAGE_ROLES and a role positioned above the one being granted.

    Args:
        guild_id: The server (guild) ID, as a string of digits.
        user_id: The member to give the role to, as a string of digits.
        role_id: The role to grant, as a string of digits.
        reason: Optional audit-log reason.

    Returns:
        JSON with ok=true echoing the guild/user/role and action="granted", or
        ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.grant_role_impl(guild_id=guild_id, user_id=user_id, role_id=role_id, reason=reason))


async def revoke_role(guild_id: str, user_id: str, role_id: str, reason: str = "") -> str:
    """Revoke a role from a member of a Discord server.

    Removes role ``role_id`` from member ``user_id`` in ``guild_id``. Requires
    the bot to have MANAGE_ROLES and a role positioned above the one removed.

    Args:
        guild_id: The server (guild) ID, as a string of digits.
        user_id: The member to take the role from, as a string of digits.
        role_id: The role to revoke, as a string of digits.
        reason: Optional audit-log reason.

    Returns:
        JSON with ok=true echoing the guild/user/role and action="revoked", or
        ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.revoke_role_impl(guild_id=guild_id, user_id=user_id, role_id=role_id, reason=reason))


async def timeout_member(guild_id: str, user_id: str, duration_minutes: int, reason: str = "") -> str:
    """Time out (mute) a member for a number of minutes, or clear a timeout.

    Applies a Discord communication timeout to member ``user_id`` in
    ``guild_id`` for ``duration_minutes`` from now; the member can't send
    messages, react, or speak until it expires. Pass ``duration_minutes=0`` to
    lift an active timeout. Requires the bot to have the MODERATE_MEMBERS
    permission. Discord caps timeouts at 28 days.

    Args:
        guild_id: The server (guild) ID, as a string of digits.
        user_id: The member to time out, as a string of digits.
        duration_minutes: Minutes from now until the timeout expires; 0 clears it.
        reason: Optional audit-log reason.

    Returns:
        JSON with ok=true and ``timed_out_until`` (ISO timestamp, or null when
        cleared), or ok=false with a ``message`` on failure.
    """
    return _dumps(
        await _d.timeout_member_impl(
            guild_id=guild_id, user_id=user_id, duration_minutes=duration_minutes, reason=reason
        )
    )


async def kick_member(guild_id: str, user_id: str, reason: str = "") -> str:
    """Kick a member from a Discord server.

    Removes member ``user_id`` from ``guild_id``. They can rejoin with a new
    invite. Requires the bot to have the KICK_MEMBERS permission and a role
    above the target member.

    Args:
        guild_id: The server (guild) ID, as a string of digits.
        user_id: The member to kick, as a string of digits.
        reason: Optional audit-log reason.

    Returns:
        JSON with ok=true echoing the guild/user and action="kicked", or
        ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.kick_member_impl(guild_id=guild_id, user_id=user_id, reason=reason))


async def ban_member(guild_id: str, user_id: str, delete_message_days: int = 0, reason: str = "") -> str:
    """Ban a user from a Discord server.

    Bans ``user_id`` from ``guild_id`` so they can't rejoin, optionally deleting
    their recent messages. Requires the bot to have the BAN_MEMBERS permission
    and a role above the target member.

    Args:
        guild_id: The server (guild) ID, as a string of digits.
        user_id: The user to ban, as a string of digits.
        delete_message_days: How many days of the user's recent messages to
            purge (0-7, default 0).
        reason: Optional audit-log reason.

    Returns:
        JSON with ok=true echoing the guild/user and action="banned", or
        ok=false with a ``message`` on failure.
    """
    return _dumps(
        await _d.ban_member_impl(
            guild_id=guild_id, user_id=user_id, delete_message_days=delete_message_days, reason=reason
        )
    )


async def unban_member(guild_id: str, user_id: str, reason: str = "") -> str:
    """Lift a ban on a user in a Discord server.

    Removes the ban on ``user_id`` in ``guild_id`` so they may be invited back.
    Requires the bot to have the BAN_MEMBERS permission.

    Args:
        guild_id: The server (guild) ID, as a string of digits.
        user_id: The user to unban, as a string of digits.
        reason: Optional audit-log reason.

    Returns:
        JSON with ok=true echoing the guild/user and action="unbanned", or
        ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.unban_member_impl(guild_id=guild_id, user_id=user_id, reason=reason))
