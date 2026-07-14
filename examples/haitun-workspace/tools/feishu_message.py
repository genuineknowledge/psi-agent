"""Feishu/Lark messaging tools — send, reply-in-thread, and list messages.

These let the bot proactively post to a group/user, form a native Feishu
**thread** (topic) by replying in-thread, and read the messages under a chat or
thread. For example: post a topic root message, then read the thread's replies
and post per-reply feedback back into the same thread.

To @-mention someone in the text, embed ``<at user_id="ou_xxx"></at>`` in the
``text`` / ``content`` string (the value is the person's open_id).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_message_send(receive_id: str, text: str, receive_id_type: str = "chat_id") -> str:
    """Send a text message to a chat or user.

    The response includes ``message_id`` and ``thread_id``. Keep the returned
    ``message_id`` if you plan to reply-in-thread to it later (it becomes the
    topic root).

    Args:
        receive_id: Target id — a chat_id (oc_...), open_id (ou_...), user_id, union_id, or email.
        text: Message text. May contain ``<at user_id="ou_xxx"></at>`` to @-mention.
        receive_id_type: Type of receive_id — chat_id, open_id, user_id, union_id, or email.
    """
    return _f.dumps_result(await _f.send_message_impl(receive_id, text, receive_id_type))


async def feishu_message_reply(message_id: str, text: str, reply_in_thread: bool = True) -> str:
    """Reply to a message; with ``reply_in_thread=True`` this forms/continues a native thread (topic).

    Args:
        message_id: The message to reply to (the topic root, or any message in the thread).
        text: Reply text. May contain ``<at user_id="ou_xxx"></at>`` to @-mention.
        reply_in_thread: True (default) keeps replies in one Feishu thread/topic.
    """
    return _f.dumps_result(await _f.reply_message_impl(message_id, text, reply_in_thread))


async def feishu_message_list(
    container_id: str,
    container_id_type: str = "chat",
    sort_type: str = "ByCreateTimeAsc",
    page_size: int = 50,
    page_token: str = "",
) -> str:
    """List messages in a chat or thread.

    To read the replies under a topic, pass ``container_id_type="thread"`` and the
    topic's ``thread_id`` as ``container_id``.

    Args:
        container_id: A chat_id (oc_...) or a thread_id, matching container_id_type.
        container_id_type: "chat" (default) or "thread".
        sort_type: "ByCreateTimeAsc" (default) or "ByCreateTimeDesc".
        page_size: Max messages to return (default 50, max 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(
        await _f.list_messages_impl(container_id, container_id_type, sort_type, page_size, page_token)
    )
