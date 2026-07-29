"""Feishu/Lark chat (group) tools — find a group the bot belongs to by name,
resolve a member's open_id, and create a new group (拉人建群).

Use ``feishu_chat_find`` to resolve a human-given group name (e.g. "主群") into a
``chat_id`` before sending messages (the bot must already be a member), or
``feishu_chat_create`` to spin up a brand-new group and pull people into it.
Pair with ``feishu_message`` (send / reply-in-thread / list messages).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_chat_find(name: str, exact: bool = False, page_size: int = 50, page_token: str = "") -> str:
    """Find Feishu/Lark groups the bot is in whose name matches ``name``.

    Returns candidate groups as ``{chat_id, name, description}``. If several
    match, all are returned — pick the right ``chat_id`` before sending.

    Args:
        name: Group name (or keyword) to search for.
        exact: When true, keep only groups whose name equals ``name`` exactly.
        page_size: Max groups to return (default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.find_chat_impl(name, exact, page_size, page_token))


async def feishu_chat_find_member(
    chat_id: str, name: str = "", exact: bool = False, member_id_type: str = "open_id"
) -> str:
    """Resolve a group member's user id (open_id) by their name.

    Feishu bots can't search all users by name, so this lists the group's members
    (each carries a name + id) and matches by name. Use it to turn a person's name
    into an ``open_id`` before @-mentioning or direct-messaging them. Pages through
    the full roster automatically.

    Returns matches as ``{name, id, member_id_type}``. If several people share the
    name, all are returned — pick the right ``id``.

    Args:
        chat_id: The group's chat_id (from ``feishu_chat_find``). The person must be a member.
        name: Person's name to match. Empty returns the whole roster.
        exact: When true, match the name exactly; otherwise substring match.
        member_id_type: Id form to return — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.find_member_id_impl(chat_id, name, exact, member_id_type))


async def feishu_chat_list_members(chat_id: str, member_id_type: str = "open_id") -> str:
    """List every member of a Feishu/Lark group in one call.

    Unlike ``feishu_chat_find_member`` (which searches by a specific name), this
    returns the group's whole roster — use it when you need everyone in the group,
    not just a matched person. Pages through the full roster automatically.

    Returns members as ``{name, id, member_id_type}`` plus a total ``count``.

    Args:
        chat_id: The group's chat_id (from ``feishu_chat_find``). The bot must be a member.
        member_id_type: Id form to return — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.list_chat_members_impl(chat_id, member_id_type))


async def feishu_chat_create(
    name: str,
    user_ids: list[str] | None = None,
    description: str = "",
    owner_id: str = "",
    user_id_type: str = "open_id",
) -> str:
    """Create a **new** Feishu/Lark group chat and pull the given people in (拉人建群).

    Use this when there is no existing group to post to — the bot creates the group,
    hands it to the **requester** as owner (``owner_id``), and stays on as an admin so
    you can still send to the returned ``chat_id`` with ``feishu_message_send``. This is
    the missing piece versus ``feishu_message_send``, which can only post to a group
    that already exists.

    Members are given as user ids, not names: resolve names to open_ids first with
    ``feishu_chat_find_member`` (from another group) or ``feishu_department_members``.
    The response includes the new ``chat_id`` and ``invalid_user_ids`` (ids Feishu
    could not add — e.g. outside the app's contact scope).

    Args:
        name: Group name (required).
        user_ids: Members to invite — a list of ids matching ``user_id_type`` (max 50).
            Empty creates a group with just the bot; invite more later.
        description: Group description/topic (optional).
        owner_id: Id (matching ``user_id_type``) of the person to make group owner.
            **Default to the requester** — pass the ``sender_open_id`` from
            ``<feishu_context>`` so the person who asked for the group owns it (the bot
            stays an admin and can keep posting). Pass someone else's id if the requester
            explicitly wants another person to be owner. Leave empty only for a
            bot-authored group with no human requester (the bot then owns it).
        user_id_type: Id form used by user_ids/owner_id — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.create_chat_impl(name, user_ids, description, owner_id, user_id_type))
