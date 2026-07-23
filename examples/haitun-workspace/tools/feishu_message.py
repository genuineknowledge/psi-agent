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


async def feishu_topic_start(
    chat_id: str, text: str, at_open_ids: list[str] | None = None, at_all: bool = False
) -> str:
    """Start a topic in a group by posting a root message, @-mentioning the given people.

    Convenience over ``feishu_message_send``: you pass the open_ids to @-mention
    (resolve names via ``feishu_chat_find_member``) and the tool builds the ``<at>``
    tags for you — no need to hand-write the tag syntax. In a topic-enabled group
    the returned ``thread_id`` is the new topic's root; reply into it with
    ``feishu_message_reply(message_id, ..., reply_in_thread=True)``.

    Args:
        chat_id: The target group's chat_id (from ``feishu_chat_find``). Must be a topic group.
        text: The topic's opening message.
        at_open_ids: Open_ids to @-mention at the start of the message (optional).
        at_all: When true, prepend an @everyone mention (group must allow @all).
    """
    return _f.dumps_result(await _f.start_topic_impl(chat_id, text, at_open_ids, at_all))


async def feishu_message_send(
    receive_id: str, text: str, receive_id_type: str = "chat_id", on_behalf_of: str = ""
) -> str:
    """Send a text message to a chat or user.

    The response includes ``message_id`` and ``thread_id``. Keep the returned
    ``message_id`` if you plan to reply-in-thread to it later (it becomes the
    topic root).

    When you are **relaying someone's words to a third party** ("帮我给张三带句话…"),
    pass that person's open_id as ``on_behalf_of`` — the recipient then sees a
    "{姓名}给你发了一条消息" attribution prefix instead of a bare bubble that looks like
    the bot spoke on its own. Use the ``sender_open_id`` from ``<feishu_context>``.
    Leave it empty for messages the bot itself authors (dashboards, notifications, etc.).

    Args:
        receive_id: Target id — a chat_id (oc_...), open_id (ou_...), user_id, union_id, or email.
        text: Message text. May contain ``<at user_id="ou_xxx"></at>`` to @-mention.
        receive_id_type: Type of receive_id — chat_id, open_id, user_id, union_id, or email.
        on_behalf_of: Open_id of the person whose words you are relaying (optional). When
            set, the text is wrapped with a "某人给你发了一条消息" attribution prefix.
    """
    return _f.dumps_result(await _f.send_message_impl(receive_id, text, receive_id_type, on_behalf_of))


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


async def feishu_thread_read(thread_id: str, page_size: int = 50) -> str:
    """Read a topic thread as clean, per-message records — sender + plain text.

    Convenience over ``feishu_message_list``: pages the whole thread and returns
    ``messages`` as ``[{message_id, sender_open_id, sender_type, create_time, text}]``,
    with text already extracted from both plain (text) and rich (post) messages.
    Ideal for scanning a topic's replies, spotting who posted what (e.g. a todo
    list), and then replying to or DMing that person by their ``sender_open_id``.

    Args:
        thread_id: The topic's thread_id (e.g. the ``thread_id`` returned by
            ``feishu_topic_start`` / ``feishu_message_send``).
        page_size: Messages per page while paging (default 50, max 50).
    """
    return _f.dumps_result(await _f.read_thread_impl(thread_id, page_size))
