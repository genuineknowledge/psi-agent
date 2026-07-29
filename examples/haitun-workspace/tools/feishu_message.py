"""Feishu/Lark messaging tools — send, reply-in-thread, recall, and list messages.

These let the bot proactively post to a group/user, form a native Feishu
**thread** (topic) by replying in-thread, take back a message that shouldn't have
been sent, and read the messages under a chat or thread. For example: post a topic
root message, then read the thread's replies and post per-reply feedback back into
the same thread.

To @-mention someone, embed ``<at user_id="ou_xxx"></at>`` in the ``text`` (the
value is the person's open_id). ``feishu_message_send`` auto-detects such tags and
sends a rich-text ``post`` so the mention renders — a raw ``<at>`` in a plain text
message would otherwise show up literally.
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
    pass that person's open_id as ``on_behalf_of`` and send it as a **private DM to
    the recipient** — set ``receive_id`` to the recipient's own open_id (``ou_...``),
    NOT a group chat_id. You may look the recipient up in a group with
    ``feishu_chat_find_member`` to get their open_id, but the message itself must go to
    their DM, never posted into the group. (As a safeguard, a relay addressed to a
    group is auto-redirected to the mentioned person's DM, or refused if no recipient
    can be determined.) The recipient sees a "{姓名}给你发了一条消息" attribution prefix.
    Use the ``sender_open_id`` from ``<feishu_context>`` as ``on_behalf_of``.
    Leave it empty for messages the bot itself authors (dashboards, notifications, etc.).

    Args:
        receive_id: Target id — a chat_id (oc_...), open_id (ou_...), user_id, union_id, or email.
        text: Message text. May contain ``<at user_id="ou_xxx"></at>`` to @-mention.
        receive_id_type: Type of receive_id — chat_id, open_id, user_id, union_id, or email.
            Usually leave as-is: the type is auto-detected from the id prefix (``oc_``→chat_id,
            ``ou_``→open_id, ``on_``→union_id, contains ``@``→email), so DMing by open_id works
            even with the default. Only set it explicitly for a bare user_id.
        on_behalf_of: Open_id of the person whose words you are relaying (optional). When
            set, the text is wrapped with a "某人给你发了一条消息" attribution prefix.
    """
    return _f.dumps_result(await _f.send_message_impl(receive_id, text, receive_id_type, on_behalf_of))


async def feishu_message_send_card(
    receive_id: str,
    card_json: str,
    receive_id_type: str = "chat_id",
    user_key: str = "",
    business_context_json: str = "{}",
    action_handlers_json: str = "{}",
) -> str:
    """Send an **interactive card** message — buttons, forms, inputs, selectors, date pickers.

    Far richer than ``feishu_message_send`` (plain text): a card can carry clickable
    buttons, a form (input fields / dropdowns / date pickers) the recipient fills in and
    submits, styled headers, multi-column layouts, images and dividers. Use it whenever
    you want the recipient to *act* (approve/reject, pick an option, submit a value)
    rather than just read.

    You build the card yourself and pass it as a JSON string in ``card_json``. Both
    Feishu card formats are accepted and sent verbatim. For interactive button
    groups and forms, the legacy format is the safest default::

        {"config": {"wide_screen_mode": true},
         "header": {"title": {"tag": "plain_text", "content": "请假审批"}, "template": "blue"},
         "elements": [
           {"tag": "markdown", "content": "**张三** 申请年假 2 天"},
           {"tag": "action", "actions": [
             {"tag": "button", "text": {"tag": "plain_text", "content": "同意"},
              "type": "primary", "value": {"action": "approve", "id": "req_1"}},
             {"tag": "button", "text": {"tag": "plain_text", "content": "驳回"},
              "type": "danger", "value": {"action": "reject", "id": "req_1"}}]}]}

    Card 2.0 (``{"schema": "2.0", ...}``) is also accepted, but it does **not**
    support the legacy ``{"tag": "action"}`` container. Put Card 2.0-supported
    controls directly under ``body.elements`` instead of copying the legacy layout.

    Selectors / date pickers go inside an ``action`` element (``select_static`` with
    ``options``, ``date_picker``, ``picker_time``, …). When their selected value must reach
    the agent reliably, put them inside a ``form`` and use a submit action so Feishu returns
    the result in ``form_value``. The SDK's standalone selector/date callback deduplication
    does not distinguish every changed selection. Anything the Feishu 消息卡片 spec supports
    is still sent as-is.

    Button/form actions are delivered back to the operator's agent session as the next
    structured user turn, encoded as JSON inside ``<feishu_card_action>``. When sending to
    another person, provide both ``business_context_json`` and ``action_handlers_json`` so
    their agent receives the full original card, source Session/user, business facts, and a
    deterministic dispatch result. The Channel selects the handler but does not execute it or
    bypass the LLM. Handler-map keys, handler identifiers, and callback action IDs must be
    canonical strings without surrounding whitespace and are matched exactly. With a non-empty
    handler map, an unknown action produces
    ``matched=false`` and ``handler=null``; the recipient agent must not invent or execute an
    unmatched handler. Only a successfully loaded v1/v2 snapshot that confirms there was no
    handler map may fall back to using ``value.action`` / ``action_id`` as the handler for
    compatibility. Missing or invalid snapshots fail closed. The first callback leaves a durable
    consumed tombstone, so later callbacks are ignored across Channel processes and restarts.

    When handling the resulting ``<feishu_card_action>``, the updated original card already
    acknowledges the selected option. Do not narrate the click or announce a planned action before
    calling the matched handler. After the handler succeeds, finish with zero assistant content:
    do not emit ``NO_REPLY`` or a success confirmation. Reply only when the operator still needs a
    warning, partial-failure detail, permission problem, or required next step. An unmatched or
    failed handler must never be reported as successful.

    Every actionable element's ``value`` must include an explicit action name and a stable
    business identifier such as ``request_id``; different buttons need different values.
    Before a consequential operation, re-check authorization and current business state;
    keep the underlying operation idempotent because delivery is at-least-once. A card is
    single-use: the first accepted button/form action preserves its original content, replaces
    its interactive region with a read-only selected-value note, and ignores later actions from
    the same card. Send a new card when the user must submit another response.

    After a successful call, the card is already visible to the recipient. If it carries all
    necessary user-facing information, finish with zero assistant content: do not emit ``NO_REPLY``,
    confirm delivery, or repeat its content and button labels. If necessary information is not
    already conveyed by the card, such as a warning, partial failure, or required next step, reply
    with only that information; never suppress it.

    If the card is sent but its callback snapshot cannot be saved, the result is
    ``ok=false, sent=true, callback_context_saved=false``. Report that necessary partial failure,
    but do not retry the send and create a duplicate card. A custom Feishu Channel AppData root
    must match the Gateway/workspace-tool root; prefer setting ``PSI_APPDATA`` for both processes.

    Args:
        receive_id: Target id — a chat_id (oc_...), open_id (ou_...), user_id, union_id, or email.
        card_json: The full Feishu card as a JSON object string (see examples above).
        receive_id_type: Type of receive_id — chat_id, open_id, user_id, union_id, or email.
            Auto-detected from the id prefix (oc_→chat_id, ou_→open_id, ...); only set it
            explicitly for a bare user_id.
        user_key: The sender's open_id (from ``<feishu_context>``) as a fallback identity;
            harmless to pass, leave empty in single-user scenarios.
        business_context_json: JSON object with the business facts the recipient's agent needs
            when handling a click, such as request type, request ID, requester, authorization
            facts, and current state. Do not rely on the recipient having the sender's history.
            Must be a JSON object string; falsey non-string values are rejected.
        action_handlers_json: JSON object mapping each ``value.action`` to a deterministic handler
            identifier, for example ``{"approve":"approval_decide","reject":"approval_decide"}``.
            Include every allowed action; unmatched configured actions are deliberately not
            dispatched. Keys and values must be non-empty canonical strings without surrounding
            whitespace.
    """
    if business_context_json == "{}" and action_handlers_json == "{}":
        result = await _f.send_card_impl(receive_id, card_json, receive_id_type, user_key or None)
    else:
        result = await _f.send_card_impl(
            receive_id,
            card_json,
            receive_id_type,
            user_key or None,
            business_context_json,
            action_handlers_json,
        )
    return _f.dumps_result(result)


async def feishu_message_reply(message_id: str, text: str, reply_in_thread: bool = True) -> str:
    """Reply to a message; with ``reply_in_thread=True`` this forms/continues a native thread (topic).

    Args:
        message_id: The message to reply to (the topic root, or any message in the thread).
        text: Reply text. May contain ``<at user_id="ou_xxx"></at>`` to @-mention.
        reply_in_thread: True (default) keeps replies in one Feishu thread/topic.
    """
    return _f.dumps_result(await _f.reply_message_impl(message_id, text, reply_in_thread))


async def feishu_message_recall(message_id: str, user_key: str = "") -> str:
    """Recall (unsend) a message — it disappears for everyone in the chat.

    Use this when a message the bot sent was wrong, premature, or went to the wrong
    place ("把刚才那条撤回", "刚发错了, 撤销一下"). Recalling is not editing: to correct
    the content, recall the bad message and send a new one.

    The bot can always recall **its own** messages. Recalling *someone else's* message
    requires the bot (or the identity you pass via ``user_key``) to be that group's
    owner/admin — otherwise Feishu refuses with code 230026. Recall also expires:
    beyond the tenant's admin-configured recall window it fails with 230009. In both
    cases the result carries a ``hint`` saying which limit was hit.

    Args:
        message_id: The message to recall (``om_...``). Take it from the ``message_id``
            returned by ``feishu_message_send`` / ``feishu_message_send_card`` /
            ``feishu_message_reply``, from ``<feishu_context>``, or from a
            ``feishu_message_list`` / ``feishu_thread_read`` item. A chat_id (``oc_...``)
            or open_id (``ou_...``) is not a message id and is rejected.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to recall as
            that user, which is what makes recalling *another* person's message possible
            when they are the group owner/admin; empty uses the bot's own tenant identity
            (tenant is always tried first regardless).
    """
    return _f.dumps_result(await _f.recall_message_impl(message_id, user_key))


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


async def feishu_image_get(
    message_id: str,
    file_key: str,
    save_path: str,
    resource_type: str = "image",
    user_key: str = "",
) -> str:
    """Download an image (or file) attached to a chat message to a local path.

    When someone sends a picture in Feishu, the image lives *inside* that message,
    not in Drive — so it is fetched by the message it belongs to via
    ``im/v1/messages/:message_id/resources/:file_key`` (not the drive-medias
    endpoint that ``feishu_file_download`` uses).

    Where to get ``file_key``:
    - The image the user just sent is already auto-downloaded and attached to the
      turn — you usually don't need this tool for it.
    - For an image found in history, read the chat/thread with
      ``feishu_message_list`` / ``feishu_thread_read``, then parse the message's
      content JSON: an image message has ``{"image_key": "img_v3_..."}``; a
      file/audio/video message has ``{"file_key": "file_v3_...", ...}``.

    After downloading, describe or OCR the image with the ``describe_image`` /
    ``read_pdf`` tools or the ``ocr-and-documents`` skill.

    Args:
        message_id: The message the image/file belongs to (om_...). Use the
            ``message_id`` of the message that carried the image, from
            ``<feishu_context>`` or a ``feishu_message_list`` item.
        file_key: The ``image_key`` (image message) or ``file_key`` (file/media
            message) from the message content JSON.
        save_path: Local filesystem path to write the image to (parent dirs created).
        resource_type: "image" for an image message (default), or "file" for a
            file/audio/video/media attachment.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to fetch
            as that user when the bot can't see the message; empty uses the bot's
            tenant token (tenant is always tried first regardless).
    """
    return _f.dumps_result(await _f.get_message_image_impl(message_id, file_key, save_path, resource_type, user_key))
