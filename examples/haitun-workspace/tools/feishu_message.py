"""Feishu/Lark messaging tools — send, edit, react to, reply-in-thread, recall, and list messages.

These let the bot proactively post to a group/user, form a native Feishu
**thread** (topic) by replying in-thread, take back a message that shouldn't have
been sent, and read the messages under a chat or thread. For example: post a topic
root message, then read the thread's replies and post per-reply feedback back into
the same thread.

Beyond plain text, a message can carry an image, a file, a voice clip, a video, rich
text or an interactive card — each with its own tool below, because the two-step
upload dance and the ``image_key`` / ``file_key`` split are exactly what goes wrong
when it's left to the caller.

Fixing a sent message is ``feishu_message_edit`` (content changes in place, the
``message_id`` survives), not recall-and-resend. Acknowledging one without adding to
the conversation is ``feishu_message_react``.

Around a message that already exists there are three more moves: ask who has
**read** it (``feishu_message_read_status`` — and who hasn't, which Feishu itself
won't tell you), **pin** it to the top of the chat (``feishu_message_pin`` /
``_unpin`` / ``_pins``), and **forward** it elsewhere with its author and
attachments preserved (``feishu_message_forward``, or ``_merge_forward`` for a
whole stretch of conversation as one card).

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


async def feishu_message_edit(message_id: str, text: str, user_key: str = "") -> str:
    """Edit an **already-sent** message in place — no recall, no re-send.

    Use this whenever a sent message's *content* was wrong ("把刚才那条改成…",
    "数字写错了", "补一句"): the bubble keeps its ``message_id``, its position in the
    chat and its thread, and Feishu just marks it 已编辑. Recall+resend loses the id
    (breaking replies and threads that point at it) and shows everyone a
    "撤回了一条消息" notice, so prefer editing and keep ``feishu_message_recall`` for
    messages that should not exist at all.

    Editing replaces the **whole** content, so pass the full corrected text, not a diff.
    ``<at user_id="ou_xxx"></at>`` works here too (sent as rich text so the mention renders).

    Only text and rich-text messages can be edited. An interactive card has its own tool
    (``feishu_message_edit_card``); image/file/audio/video messages cannot be edited at
    all and do have to be recalled and re-sent. Three limits are worth knowing before
    promising the user anything: only the **sender** may edit (the bot can only edit its
    own messages), a message can be edited **20 times** at most, and the tenant admin
    configures how long a message stays editable. Each failure comes back with a ``hint``
    naming the blocker — relay it instead of retrying or claiming success.

    Args:
        message_id: The message to edit (``om_...``) — from ``feishu_message_send``'s
            return, ``<feishu_context>``, or a ``feishu_message_list`` item. A chat_id
            (``oc_...``) / open_id (``ou_...``) is not a message id and is rejected.
        text: The new full message text. May contain ``<at user_id="ou_xxx"></at>``.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to edit as
            that user, which is what makes editing **their own** message possible;
            empty uses the bot's tenant identity (tenant is always tried first).
    """
    return _f.dumps_result(await _f.edit_message_impl(message_id, text, user_key))


async def feishu_message_edit_card(message_id: str, card_json: str, user_key: str = "") -> str:
    """Update an already-sent **interactive card** in place, keeping its ``message_id``.

    The card counterpart of ``feishu_message_edit`` (cards use a different endpoint).
    Use it to reflect new state on a card the recipient already has — mark an approval
    已通过, grey out buttons after a decision, refresh a dashboard — instead of sending a
    second card that leaves the stale one clickable.

    Pass the **whole** replacement card in ``card_json``, same formats as
    ``feishu_message_send_card``. Its ``config.update_multi`` is set automatically for
    legacy cards (without it Feishu updates the card for only one viewer).

    The card's **callback context is not re-registered**: button handlers were
    snapshotted when the card was sent and are consumed on first click. So this changes
    what the card *shows*, not what its buttons dispatch — if the available actions must
    change, send a new card with ``feishu_message_send_card``. Cards are updatable for
    14 days and only by the identity that sent them.

    Args:
        message_id: The card message to update (``om_...``).
        card_json: The full new Feishu card as a JSON object string.
        user_key: The sender's open_id (from ``<feishu_context>``) as a fallback
            identity; empty uses the bot's tenant identity.
    """
    return _f.dumps_result(await _f.edit_card_impl(message_id, card_json, user_key))


async def feishu_message_react(message_id: str, emoji_type: str = "THUMBSUP", user_key: str = "") -> str:
    """Add an emoji **reaction** to a message — acknowledge without sending a message.

    The right answer to "收到就行"/"给这条点个赞"/"标记一下已处理": a reaction lands on
    the existing bubble and adds nothing to the chat, where a "好的" message would.

    ``emoji_type`` takes a Feishu key (``THUMBSUP``, ``OK``, ``DONE``, ``OnIt``,
    ``THANKS``, ``Fire``, ``PARTY``), a Chinese word (``赞``, ``收到``, ``完成``,
    ``感谢``) or the emoji itself (``👍``, ``✅``, ``🎉``) — all are mapped to the key
    Feishu wants, whose casing is irregular (``THUMBSUP`` but ``Fire``, ``OnIt``) and
    otherwise fails with 231001.

    Returns a ``reaction_id``; ``feishu_message_unreact`` can also find it from the emoji.

    Args:
        message_id: The message to react to (``om_...``).
        emoji_type: Which emoji (default ``THUMBSUP``).
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to react as
            that person; empty reacts as the bot.
    """
    return _f.dumps_result(await _f.add_reaction_impl(message_id, emoji_type, user_key))


async def feishu_message_unreact(
    message_id: str, emoji_type: str = "", reaction_id: str = "", user_key: str = ""
) -> str:
    """Remove an emoji reaction from a message ("把那个赞取消").

    Address it either by ``emoji_type`` (looked up on the message, so the same argument
    that added it removes it) or by an exact ``reaction_id``. Only the identity that
    added a reaction can remove it, so pass the same ``user_key`` used to add it.

    If several people reacted with that emoji, nothing is deleted and the candidate
    ``reaction_id``s are returned — pick one rather than having someone else's reaction
    removed by guess.

    Args:
        message_id: The message to remove a reaction from (``om_...``).
        emoji_type: The emoji to take back (``THUMBSUP`` / ``赞`` / ``👍``). Optional if
            ``reaction_id`` is given.
        reaction_id: The exact reaction to delete, from ``feishu_message_react`` or
            ``feishu_message_reactions``.
        user_key: The open_id whose reaction is being removed; empty means the bot's own.
    """
    return _f.dumps_result(await _f.remove_reaction_impl(message_id, emoji_type, reaction_id, user_key))


async def feishu_message_reactions(
    message_id: str, emoji_type: str = "", page_size: int = 50, page_token: str = "", user_key: str = ""
) -> str:
    """List a message's emoji reactions — who reacted with what.

    Use it to read a lightweight poll or roll-call ("谁点了收到"), or to get the
    ``reaction_id`` needed to remove a specific reaction.

    Args:
        message_id: The message to inspect (``om_...``).
        emoji_type: Only list this emoji (optional; empty lists all).
        page_size: Reactions per page (default 50, max 50).
        page_token: Pagination cursor from a previous call's ``page_token``.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(await _f.list_reactions_impl(message_id, emoji_type, page_size, page_token, user_key))


async def feishu_message_send_image(
    receive_id: str, image_path: str, receive_id_type: str = "chat_id", user_key: str = ""
) -> str:
    """Send a local **image** as a picture message (uploads it first).

    The one way to put a picture in a chat: a URL in a text message stays a link, and a
    drive file is not a message attachment. Use it for charts you just rendered,
    screenshots, or a photo the user asked to be forwarded.

    Handles both halves (upload → send) so the ``image_key`` can't get crossed with a
    file_key. Max 10MB; JPG/JPEG/PNG/WEBP/GIF/BMP/ICO/TIFF/HEIC.

    Args:
        receive_id: Target — chat_id (``oc_...``), open_id (``ou_...``), user_id,
            union_id or email. Type is auto-detected from the prefix.
        image_path: Local path to the picture.
        receive_id_type: Only set explicitly for a bare user_id.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(
        await _f.send_media_message_impl(receive_id, image_path, "image", receive_id_type, user_key=user_key)
    )


async def feishu_message_send_file(
    receive_id: str,
    file_path: str,
    receive_id_type: str = "chat_id",
    file_name: str = "",
    user_key: str = "",
) -> str:
    """Send a local **file** as a chat attachment — PDF/Word/Excel/PPT/zip/anything.

    The recipient gets a real downloadable attachment in the chat. For a file that
    should live in the cloud drive instead (to be shared by link or edited), use
    ``feishu_drive_upload``. Max 30MB.

    Args:
        receive_id: Target — chat_id, open_id, user_id, union_id or email.
        file_path: Local path to the file.
        receive_id_type: Only set explicitly for a bare user_id.
        file_name: Display name in the chat (defaults to the file's own name).
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(
        await _f.send_media_message_impl(
            receive_id, file_path, "file", receive_id_type, file_name=file_name, user_key=user_key
        )
    )


async def feishu_message_send_audio(
    receive_id: str,
    audio_path: str,
    receive_id_type: str = "chat_id",
    duration_ms: int = 0,
    user_key: str = "",
) -> str:
    """Send a local **audio** file as a playable voice message.

    Feishu only plays ``audio`` messages that are genuinely **OPUS**; an .mp3 sent as
    audio is rejected (230055). Convert first::

        ffmpeg -i in.mp3 -acodec libopus -ac 1 -ar 16000 out.opus

    — or send the .mp3 with ``feishu_message_send_file`` as a plain attachment. The
    ``text_to_speech`` tool produces MP3, so it needs converting before it can be a
    voice message.

    Args:
        receive_id: Target — chat_id, open_id, user_id, union_id or email.
        audio_path: Local path to the .opus file.
        receive_id_type: Only set explicitly for a bare user_id.
        duration_ms: Length in milliseconds; shown next to the voice bubble when given.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(
        await _f.send_media_message_impl(
            receive_id, audio_path, "audio", receive_id_type, duration_ms=duration_ms, user_key=user_key
        )
    )


async def feishu_message_send_video(
    receive_id: str,
    video_path: str,
    receive_id_type: str = "chat_id",
    cover_image_path: str = "",
    duration_ms: int = 0,
    user_key: str = "",
) -> str:
    """Send a local **video** (mp4) as a playable video message, optionally with a cover.

    The video must be **mp4** (Feishu's only video type). Max 30MB — a larger one goes
    to the drive via ``feishu_drive_upload`` and gets shared as a link instead.

    Without ``cover_image_path`` the video shows no preview frame. If a cover is given
    but its upload fails, the video is still sent (coverless) rather than lost.

    Args:
        receive_id: Target — chat_id, open_id, user_id, union_id or email.
        video_path: Local path to the .mp4 file.
        receive_id_type: Only set explicitly for a bare user_id.
        cover_image_path: Local image used as the thumbnail (optional).
        duration_ms: Length in milliseconds, shown on the video bubble.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(
        await _f.send_media_message_impl(
            receive_id,
            video_path,
            "media",
            receive_id_type,
            cover_image_path=cover_image_path,
            duration_ms=duration_ms,
            user_key=user_key,
        )
    )


async def feishu_message_send_post(
    receive_id: str,
    blocks_json: str,
    title: str = "",
    receive_id_type: str = "chat_id",
    user_key: str = "",
) -> str:
    """Send a **rich text** message: styled text, links, mentions and images in one bubble.

    Use it when a plain text message can't carry the shape — a titled weekly report
    with a bold summary, a chart inline with its commentary, a checklist with links and
    @-mentions. Unlike a card it needs no card JSON and no callback wiring; unlike
    several separate messages it stays one bubble.

    ``blocks_json`` is a JSON array of blocks, in order::

        [{"tag": "text", "text": "本周进展", "style": ["bold"]},
         {"tag": "at", "user_id": "ou_xxx"},
         {"tag": "a", "text": "看板", "href": "https://example.com"},
         {"tag": "img", "image_path": "C:/tmp/chart.png"},
         {"tag": "md", "text": "1. 第一项\\n2. 第二项"},
         {"tag": "hr"}]

    Tags: ``text`` (optional ``style``: bold/italic/underline/lineThrough), ``a``
    (``href``), ``at`` (``user_id``, ``"all"`` for everyone), ``img``
    (``image_path`` for a local file — uploaded here — or an existing ``image_key``),
    ``code_block`` (optional ``language``), ``md`` (Markdown), ``hr``.
    Paragraph grouping is handled for you: images, separators and markdown each take
    their own line, adjacent text/link/mention nodes share one.

    Args:
        receive_id: Target — chat_id, open_id, user_id, union_id or email.
        blocks_json: JSON array of blocks as above.
        title: Optional title shown above the content.
        receive_id_type: Only set explicitly for a bare user_id.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(await _f.send_post_message_impl(receive_id, blocks_json, title, receive_id_type, user_key))


async def feishu_message_upload_image(image_path: str, user_key: str = "") -> str:
    """Upload a local image to Feishu **without sending it**, returning its ``image_key``.

    ``feishu_message_send_image`` uploads and sends in one step and is what you want for
    "把这张图发给他". Use *this* when the same picture is needed **more than once**, since
    an ``image_key`` is reusable and re-uploading the same file wastes a call each time:

    - sending one chart to several people or groups — upload once, then pass the
      ``image_key`` in each ``feishu_message_send_post`` block (``{"tag": "img",
      "image_key": "img_v3_..."}``);
    - putting a picture inside an **interactive card** (``feishu_message_send_card``),
      whose ``img`` element takes only an ``image_key`` and has no upload of its own.

    The key is an IM key and is **not** interchangeable with a drive ``file_token`` from
    ``feishu_drive_upload`` (a drive token can't be sent in a message, and an
    ``image_key`` can't live in a document), nor with the ``file_key`` from
    ``feishu_message_upload_file`` — swapping those two is Feishu error 230001.

    Max 10MB; JPG/JPEG/PNG/WEBP/GIF/BMP/ICO/TIFF/HEIC. Larger or other formats go
    through ``feishu_message_upload_file`` / ``feishu_drive_upload`` instead.

    Args:
        image_path: Local path to the picture.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(await _f.upload_image_impl(image_path, user_key))


async def feishu_message_upload_file(
    file_path: str,
    file_type: str = "",
    file_name: str = "",
    duration_ms: int = 0,
    user_key: str = "",
) -> str:
    """Upload a local file/audio/video to Feishu **without sending it**, returning its ``file_key``.

    ``feishu_message_send_file`` / ``_send_audio`` / ``_send_video`` upload and send in
    one step — use those for a one-off delivery. Use *this* when the same attachment goes
    to **several** conversations (upload once, reuse the ``file_key``) or when you want to
    verify the upload succeeded before deciding where to send it.

    ``file_type`` is Feishu's own enum, not the extension, and is derived from the suffix
    when omitted (``.mp4``→mp4, ``.pdf``→pdf, ``.docx``→doc, ``.xlsx``→xls,
    ``.pptx``→ppt, ``.opus``→opus; anything unmapped → ``stream``, which is how a
    .zip/.csv/.txt is sent). A file uploaded as ``stream`` is sent as a ``file`` message;
    ``opus`` is required for a playable voice message (an .mp3 sent as audio is rejected
    with 230055 — convert first: ``ffmpeg -i in.mp3 -acodec libopus -ac 1 -ar 16000
    out.opus``) and ``mp4`` for a video message.

    The returned ``file_key`` is **not** an ``image_key`` (pictures go through
    ``feishu_message_upload_image``; crossing the two is error 230001) and **not** a drive
    ``file_token`` from ``feishu_drive_upload``.

    Max 30MB — a bigger file belongs in the cloud drive (``feishu_drive_upload``) and gets
    shared as a link.

    Args:
        file_path: Local path to the file.
        file_type: Feishu's type enum — opus/mp4/pdf/doc/xls/ppt/stream. Leave empty to
            derive it from the file's suffix.
        file_name: Name to store/display it as (defaults to the file's own name).
        duration_ms: Length in milliseconds, for audio/video.
        user_key: The sender's open_id as a fallback identity (optional).
    """
    return _f.dumps_result(await _f.upload_file_impl(file_path, file_type, file_name, duration_ms, user_key))


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


async def feishu_message_search(
    query: str,
    chat_ids: list[str] | None = None,
    from_ids: list[str] | None = None,
    message_type: str = "",
    from_type: str = "",
    chat_type: str = "",
    start_time: str = "",
    end_time: str = "",
    limit: int = 20,
    user_key: str = "",
) -> str:
    """Search Feishu/Lark **message content** by keyword across chats (全局消息搜索).

    The one way to answer 「上周谁说过发布时间」/「搜一下关于报销的消息」 without knowing which
    group it was in: ``feishu_message_list`` can only walk one chat you already have the
    id for, this searches by words.

    Searches **as the asking person**, so it finds what they can see — Feishu accepts no
    bot token here, which is why ``user_key`` is required rather than optional, and why
    there is no bot-wide variant to fall back on. The person must have authorized once
    (``feishu_auth_card`` / ``feishu_auth_start``).

    Feishu returns message ids only, so each hit is read back: results carry the actual
    ``text``, ``chat_id``, sender and time. Hits the bot cannot read come back with
    ``readable: false`` and just their id — normally a chat the bot isn't in — rather than
    being silently dropped, so a partial answer is visible as partial.

    Args:
        query: The keyword(s) to search for.
        chat_ids: Restrict to these chats (``oc_...``); empty searches all the person's chats.
        from_ids: Restrict to messages sent by these people (ids, not names).
        message_type: Restrict to messages **carrying an attachment** — ``file`` / ``image``
            / ``media``. Leave empty to search ordinary text messages.
        from_type: ``user`` or ``bot`` — who sent it.
        chat_type: ``group_chat`` (群聊) or ``p2p_chat`` (单聊).
        start_time: Earliest send time as a **second**-level Unix timestamp string (e.g.
            ``"1609296809"``). Milliseconds are refused — they would match nothing.
        end_time: Latest send time, same format.
        limit: Max hits to return and read back (default 20, cap 50).
        user_key: The caller's open_id from ``<feishu_context>``. **Required.**
    """
    return _f.dumps_result(
        await _f.search_messages_impl(
            query,
            chat_ids,
            from_ids,
            message_type,
            from_type,
            chat_type,
            start_time,
            end_time,
            limit,
            user_key,
        )
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


async def feishu_message_read_status(
    message_id: str, include_unread: bool = True, page_size: int = 100, user_key: str = ""
) -> str:
    """Check who has **read** a message the bot sent — and who hasn't ("谁还没看").

    The answer to "这条通知大家看了吗", "还有谁没读", "催一下没看的人". Returns
    ``read_users`` (each with the millisecond ``read_time``) and, unless you turn it
    off, ``unread_users`` with names.

    Feishu only exposes the **readers**; there is no unread endpoint. So the unread
    list is computed here by subtracting the readers from the chat's member roster
    (the sender is excluded from both). That part is best-effort: if the chat's
    roster can't be listed, the read list is still returned along with a ``note``
    explaining why the unread half is missing — so check for ``unread_users``
    before reporting a count rather than assuming zero means everyone read it.

    Two hard limits are Feishu's, not ours, and both come back as a ``hint``:
    only messages the **bot itself sent** can be queried (someone else's message
    gives 230012 — there is no way around it), and only within **7 days** of
    sending (230033). Don't promise a read report on an older or third-party
    message; say it isn't available.

    Args:
        message_id: The message to check (``om_...``) — from ``feishu_message_send``'s
            return value, ``<feishu_context>``, or a ``feishu_message_list`` item.
            Must be a message the bot sent, within the last 7 days.
        include_unread: True (default) also lists who hasn't read it, by diffing
            against the group roster. Set False to skip the extra roster calls when
            only the reader list matters (or in a large group, to keep it cheap).
        page_size: Readers fetched per request (1-100, default 100). All pages are
            walked regardless, so this only tunes the request count.
        user_key: The sender's open_id (from ``<feishu_context>``) as a permission
            fallback; empty uses the bot's own tenant identity (tried first anyway).
    """
    return _f.dumps_result(await _f.read_status_impl(message_id, include_unread, page_size, user_key))


async def feishu_message_pin(message_id: str, user_key: str = "") -> str:
    """**Pin** a message to the top of its chat ("置顶这条", "把这条钉在群顶上").

    A pin puts the message in the group's 置顶 bar where everyone sees it on open —
    the right move for a notice, a meeting link or a rule that new arrivals need,
    and better than re-sending the message so it doesn't scroll away.

    Pinning is idempotent: a message that's already pinned comes back ok with the
    existing pin's info, so re-pinning is safe.

    The common failure is 230046 — many groups only let the **owner/admin** pin.
    The bot usually isn't one, so pass that person's ``user_key`` (they must have
    authorized) to pin as them, or ask the owner to open the permission. The
    returned ``hint`` says which of the two applies.

    Args:
        message_id: The message to pin (``om_...``). A chat_id (``oc_...``) is not a
            message id and is rejected.
        user_key: The open_id of the person to pin as (from ``<feishu_context>``) —
            what makes pinning possible in an owner/admin-only group; empty pins as
            the bot (tenant identity is tried first regardless).
    """
    return _f.dumps_result(await _f.pin_message_impl(message_id, user_key))


async def feishu_message_unpin(message_id: str, user_key: str = "") -> str:
    """Remove a message's pin ("取消置顶", "把置顶撤了").

    Note that Feishu also reports success when the message was **never pinned**, so
    a successful result means "not pinned now", not "a pin was removed" — don't
    claim you took down a pin you never confirmed existed. ``feishu_message_pins``
    tells you what is actually pinned.

    Same owner/admin restriction as pinning (230046): pass a ``user_key`` to act as
    someone with the right.

    Args:
        message_id: The pinned message to unpin (``om_...``), e.g. from
            ``feishu_message_pins``.
        user_key: The open_id to act as; empty uses the bot's own identity.
    """
    return _f.dumps_result(await _f.unpin_message_impl(message_id, user_key))


async def feishu_message_pins(
    chat_id: str,
    start_time: str = "",
    end_time: str = "",
    page_size: int = 50,
    page_token: str = "",
    user_key: str = "",
) -> str:
    """List a group's **pinned** messages, newest pin first ("群里置顶了什么").

    Use it to review what's currently pinned before adding another, to find the
    ``message_id`` needed by ``feishu_message_unpin``, or to answer "置顶的公告是哪条".

    Only pin **records** come back — ``message_id``, who pinned it, and when. The
    pinned messages' text is not included; read it with ``feishu_message_list`` if
    the content matters.

    Args:
        chat_id: The group whose pins you want (``oc_...``, from ``feishu_chat_find``
            or ``<feishu_context>``). Pins can only be listed per group.
        start_time: Only pins created at/after this millisecond timestamp (optional;
            empty reaches back to the group's oldest pin).
        end_time: Only pins created before this millisecond timestamp (optional;
            empty starts from the newest). Must be greater than ``start_time``.
        page_size: Pins per page (1-50, default 50).
        page_token: Continue from a previous call's ``page_token`` (when ``has_more``).
        user_key: The sender's open_id as a permission fallback; empty uses the bot's.
    """
    return _f.dumps_result(await _f.list_pins_impl(chat_id, start_time, end_time, page_size, page_token, user_key))


async def feishu_message_forward(
    message_id: str, receive_id: str, receive_id_type: str = "chat_id", user_key: str = ""
) -> str:
    """**Forward** a message to another chat, person or thread ("转发给…", "同步到那个群").

    Forwarding keeps the original's **author and content intact** — the recipient
    sees who said it. That's the difference from reading the text and re-sending it
    with ``feishu_message_send``, which silently drops the attribution and any
    image/file the message carried. Use forwarding whenever the original matters as
    evidence ("把客户这句话转给研发"); the content can't be modified in the process,
    so add your own remark as a separate message afterwards.

    Not everything can be forwarded: red packets, polls, voice messages, calendar
    handovers, system and encrypted messages are refused (230061), as are messages
    already recalled (230065) and sub-messages inside a 合并转发 bundle (230064).
    Each comes back with a ``hint`` — relay it rather than retrying.

    To forward **several** messages at once as one bundle, use
    ``feishu_message_merge_forward``.

    Args:
        message_id: The message to forward (``om_...``).
        receive_id: Where to forward it — a group ``chat_id`` (``oc_...``), a person's
            ``open_id`` (``ou_...``) for a DM, a ``union_id`` (``on_...``), an email,
            or a ``thread_id`` (``omt_...``) to drop it into a topic.
        receive_id_type: Type of ``receive_id``. Usually leave as-is: it's inferred
            from the prefix (``oc_``→chat_id, ``ou_``→open_id, ``on_``→union_id,
            ``omt_``→thread_id, contains ``@``→email). Only set it for a bare user_id.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to forward
            as that person (e.g. when the bot can't see the source message); empty
            forwards as the bot.
    """
    return _f.dumps_result(await _f.forward_message_impl(message_id, receive_id, receive_id_type, user_key))


async def feishu_message_merge_forward(
    message_ids_json: str, receive_id: str, receive_id_type: str = "chat_id", user_key: str = ""
) -> str:
    """Forward **several messages as one bundle** (合并转发) — a single collapsed card.

    Better than forwarding one-by-one when you're passing on a *conversation*
    ("把刚才那段讨论转给老板"): the recipient gets one 合并转发 card holding all of
    them in order, instead of N separate bubbles, and each keeps its own author.

    All the ids must come from the **same conversation** — Feishu refuses a batch
    that spans groups (230069), and won't mix a topic's replies with ordinary
    messages (230067). Individual ids it rejects are returned in
    ``invalid_message_ids`` rather than dropped silently, so check that field
    before reporting how many went through.

    Args:
        message_ids_json: The messages to bundle, as a JSON array of ``om_...`` ids
            (1-100, all from one chat), e.g. ``["om_a", "om_b", "om_c"]``. Collect
            them from ``feishu_message_list`` / ``feishu_thread_read``. A plain
            comma-separated list is also accepted.
        receive_id: The target — ``chat_id`` (``oc_...``), ``open_id`` (``ou_...``),
            ``union_id``, email, or ``thread_id`` (``omt_...``).
        receive_id_type: Type of ``receive_id``; inferred from its prefix, so it
            usually needs no change.
        user_key: The open_id to forward as; empty forwards as the bot.
    """
    return _f.dumps_result(
        await _f.merge_forward_messages_impl(message_ids_json, receive_id, receive_id_type, user_key)
    )
