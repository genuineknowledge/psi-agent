"""Discord toolset — read and participate in a Discord server.

Exposes one tool per Discord action so the agent can browse and take part in a
server it has been invited to: ``search_members``, ``fetch_messages``,
``send_message``, ``react``, ``fetch_channel``, and ``list_channels``. Each is
a thin async wrapper over the Discord HTTP API (via hikari's REST-only client);
the real logic lives in ``_discord_impl`` so the heavy third-party import is
kept out of the tool-discovery path.

Auth: set ``DISCORD_BOT_TOKEN`` in the environment to a bot token. Invite the
bot to the target server first; ``search_members`` additionally needs the
privileged ``GUILD_MEMBERS`` intent enabled for the bot. IDs (guild/channel/
message) are Discord snowflakes — pass them as strings.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import json

import _discord_impl as _d


def _dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


async def search_members(guild_id: str, query: str, limit: int = 25) -> str:
    """Search a Discord server's members by name and return the matches.

    Looks up members of ``guild_id`` whose username or nickname begins with
    ``query`` (Discord matches on a prefix, case-insensitively). Use it to
    resolve a person's user ID from a partial name before mentioning or
    messaging them. Requires the bot to have the privileged GUILD_MEMBERS
    intent enabled.

    Args:
        guild_id: The server (guild) ID to search within, as a string of digits.
        query: The name prefix to match against usernames and nicknames.
        limit: Maximum number of members to return (default 25, capped at 100).

    Returns:
        JSON with ok=true and a ``members`` list ({id, username, display_name,
        nickname, is_bot}), or ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.search_members_impl(guild_id=guild_id, query=query, limit=limit))


async def fetch_messages(channel_id: str, limit: int = 20) -> str:
    """Fetch the most recent messages from a Discord channel (newest first).

    Reads the latest messages in ``channel_id`` so you can catch up on a
    conversation before replying. Returns author, text content, timestamp, and
    any attachment URLs for each message.

    Args:
        channel_id: The channel ID to read from, as a string of digits.
        limit: Maximum number of messages to return (default 20, capped at 100).

    Returns:
        JSON with ok=true and a ``messages`` list ({id, channel_id, author,
        content, timestamp, attachments}), or ok=false with a ``message``.
    """
    return _dumps(await _d.fetch_messages_impl(channel_id=channel_id, limit=limit))


async def send_message(channel_id: str, content: str) -> str:
    """Send a text message to a Discord channel.

    Posts ``content`` as the bot into ``channel_id``. The bot must have
    permission to send messages in that channel. Standard Discord markdown and
    ``<@user_id>`` / ``<#channel_id>`` mentions in the text are honored.

    Args:
        channel_id: The channel ID to post into, as a string of digits.
        content: The message text to send. Must not be empty.

    Returns:
        JSON with ok=true and the created ``message`` object, or ok=false with
        a ``message`` on failure.
    """
    return _dumps(await _d.send_message_impl(channel_id=channel_id, content=content))


async def react(channel_id: str, message_id: str, emoji: str) -> str:
    """Add a reaction emoji to a message in a Discord channel.

    Reacts to the message ``message_id`` in ``channel_id``. For a standard
    emoji pass the unicode character (e.g. "👍"); for a custom server emoji pass
    it in ``name:id`` form (e.g. "partyblob:12345").

    Args:
        channel_id: The channel the message lives in, as a string of digits.
        message_id: The message to react to, as a string of digits.
        emoji: The emoji to add — a unicode character, or ``name:id`` for a
            custom emoji.

    Returns:
        JSON with ok=true echoing the channel/message/emoji, or ok=false with a
        ``message`` on failure.
    """
    return _dumps(await _d.react_impl(channel_id=channel_id, message_id=message_id, emoji=emoji))


async def fetch_channel(channel_id: str) -> str:
    """Fetch metadata for a single Discord channel by its ID.

    Returns the channel's name, type, and parent/guild IDs — useful to confirm
    a channel exists and what kind it is (text, voice, thread, category, ...)
    before reading or posting.

    Args:
        channel_id: The channel ID to look up, as a string of digits.

    Returns:
        JSON with ok=true and a ``channel`` object ({id, name, type, guild_id,
        parent_id}), or ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.fetch_channel_impl(channel_id=channel_id))


async def list_channels(guild_id: str) -> str:
    """List every channel in a Discord server.

    Enumerates all channels in ``guild_id`` (text, voice, categories, and so
    on) so you can discover channel IDs to read from or post into.

    Args:
        guild_id: The server (guild) ID whose channels to list, as a string of digits.

    Returns:
        JSON with ok=true and a ``channels`` list ({id, name, type, guild_id,
        parent_id}), or ok=false with a ``message`` on failure.
    """
    return _dumps(await _d.list_channels_impl(guild_id=guild_id))
