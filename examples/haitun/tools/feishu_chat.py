"""Feishu/Lark chat (group) tools — find a group the bot belongs to by name.

Use this to resolve a human-given group name (e.g. "主群") into a ``chat_id``
before sending messages. The bot must already be a member of the group.
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
