"""Feishu IM — send/edit/recall, reactions, media, cards, chats, announcements, search, threads.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
from typing import Any

import _feishu_impl as _core
import anyio
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest
from loguru import logger

from psi_agent.channel.feishu._card_store import save_card_snapshot
from psi_agent.session.runtime_context import get_session_id

# ── IM (messaging) — find chat, send, reply-in-thread, list messages ──────────
#
# These power the "daily todo topic" schedules: find the main group by name,
# post a topic root message, reply-in-thread to form a native Feishu thread, and
# read the thread's replies. All use bot/tenant credentials (no user token).


def _infer_receive_id_type(receive_id: str, given: str) -> str:
    """Infer the Feishu ``receive_id_type`` from the id's prefix.

    The API rejects a mismatched type with ``230001 invalid receive_id`` (e.g.
    sending a DM by passing an ``ou_`` open_id while the type is still the default
    ``chat_id``). The id prefix is an unambiguous signal, so trust it: ``oc_`` is a
    chat_id, ``ou_`` an open_id, ``on_`` a union_id, and a value containing ``@`` an
    email. Only fall back to *given* when the prefix carries no signal (e.g. a bare
    user_id), so an explicit caller choice for those still wins.
    """
    rid = receive_id.strip()
    if rid.startswith("oc_"):
        return "chat_id"
    if rid.startswith("ou_"):
        return "open_id"
    if rid.startswith("on_"):
        return "union_id"
    if "@" in rid:
        return "email"
    return given


def _build_send_message_request(receive_id: str, receive_id_type: str, msg_type: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("receive_id_type", receive_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": content,
    }
    return req


async def _resolve_sender_name(open_id: str) -> str:
    """把发起人 open_id 解析成真实姓名, 供「代人带话」前缀用。

    复用 ``get_users_batch_impl`` 取 ``name``; 查名失败 / 取空 / 非 open_id 一律
    回退成传入值本身——绝不因查名失败而让消息发不出去 (转达失败比署名不全更糟)。
    """
    open_id = (open_id or "").strip()
    if not open_id:
        return ""
    try:
        res = await _core.get_users_batch_impl(open_id, user_id_type="open_id")
    except Exception:
        return open_id
    if not res.get("ok"):
        return open_id
    users = res.get("users") or []
    if users and isinstance(users[0], dict):
        name = (users[0].get("name") or "").strip()
        if name:
            return name
    return open_id


_AT_TAG_RE = re.compile(
    r"(?:<|&lt;)\s*at\b[^>]*?user_id\s*=\s*[\"']?(?P<uid>[^\"'>&\s]+)[\"']?[^>]*?(?:>|&gt;)"
    r"(?:\s*(?:<|&lt;)\s*/\s*at\s*(?:>|&gt;))?",
    re.IGNORECASE,
)


def _extract_and_strip_at_tags(text: str) -> tuple[str, list[str]]:
    """Pull ``<at user_id=ou_xxx>`` tags (also HTML-escaped ``&lt;at&gt;``) out of *text*.

    Returns the text with those tags removed and the list of mentioned open_ids.
    A plain-text message's ``<at>`` does NOT render for bots (Feishu shows the raw
    tag, e.g. ``&lt;at&gt;``), so the caller must resend as a ``post`` message whose
    ``at`` element renders. Extracting here means the model can write the tag inline
    (as the tool docs historically told it to) and mentions still work.
    """
    open_ids = [m.group("uid") for m in _AT_TAG_RE.finditer(text)]
    stripped = _AT_TAG_RE.sub("", text).strip()
    return stripped, open_ids


async def send_message_impl(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
    """Send a text message to a chat/user. Returns message_id + thread_id (thread_id is the topic root).

    When ``on_behalf_of`` (发起人的 open_id) is given, the bot is relaying someone
    else's words, so the text is wrapped with a "{姓名}给你发了一条消息" attribution
    prefix — the recipient sees who it is from instead of a bare bubble authored by
    the bot. Name is resolved from the open_id; falls back to the raw open_id if
    unresolvable.

    ``receive_id_type`` is auto-corrected from the id prefix, and any ``<at>`` tags
    embedded in *text* are turned into a real ``post`` mention (a plain-text ``<at>``
    would render as a raw tag for bots), so mentions work regardless of id type or
    how the tag was written.

    Relay guard: relaying someone's words (``on_behalf_of`` set) is a private message
    to a person, never a group post. If ``receive_id`` is a group chat (``oc_``), the
    send is redirected to the mentioned person's DM (open_id taken from the ``<at>``
    tag in *text*). If no recipient open_id can be determined, it returns an error
    instead of leaking the private message into the group.
    """
    at_target_ids = [m.group("uid") for m in _AT_TAG_RE.finditer(text)]
    if on_behalf_of.strip() and receive_id.strip().startswith("oc_"):
        # A relay must stay private: never post it into the group. Redirect to the
        # mentioned person's DM; refuse (don't fall back to the group) if unknown.
        target = next((oid for oid in at_target_ids if oid.startswith("ou_")), "")
        if not target:
            return _core._error(
                "代人带话必须私发给本人, 但未能从消息里确定收件人 open_id; "
                "请用 feishu_chat_find_member 查到本人 open_id 后作为 receive_id 私发, 不要发到群里。",
                code="relay_recipient_unknown",
            )
        receive_id, receive_id_type = target, "open_id"

    if on_behalf_of.strip():
        sender = await _resolve_sender_name(on_behalf_of)
        if sender:
            text = f"{sender}给你发了一条消息：「{text}」"  # noqa: RUF001
    receive_id_type = _infer_receive_id_type(receive_id, receive_id_type)
    stripped, at_open_ids = _extract_and_strip_at_tags(text)
    # In a 1:1 DM an @-mention is noise; keep mentions only when sending to a group.
    if at_open_ids and receive_id_type == "chat_id":
        # Mentions only render in a post message; a plain-text <at> shows the raw tag.
        content = _build_post_at_content(stripped, at_open_ids, at_all=False)
        req = _build_send_message_request(receive_id, receive_id_type, "post", content)
    else:
        content = json.dumps({"text": stripped if at_open_ids else text}, ensure_ascii=False)
        req = _build_send_message_request(receive_id, receive_id_type, "text", content)
    res = await _core._invoke(req)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
    }


async def send_card_impl(
    receive_id: str,
    card_json: str,
    receive_id_type: str,
    user_key: str | None = None,
    business_context_json: str = "{}",
    action_handlers_json: str = "{}",
    multi_use: bool = False,
) -> dict[str, Any]:
    """Send an interactive card (``msg_type=interactive``) — buttons/forms/selectors etc.

    ``card_json`` is the full card object as a JSON string (Feishu 卡片 JSON, either the
    card 2.0 ``{"schema":"2.0","body":{"elements":[...]}}`` form or the legacy
    ``{"config":...,"elements":[...]}`` form). It is parsed, validated to be a JSON
    object, and posted verbatim as the message ``content`` — so any element the Feishu
    card spec supports (button / form / input / select_static / date_picker / …) works.

    ``receive_id_type`` is auto-corrected from the id prefix, same as ``send_message_impl``.
    Returns ``message_id`` + ``thread_id`` (thread_id is the topic root if in a thread).

    ``multi_use=True`` makes each action consumable **independently** (a TODO list whose
    rows are ticked one at a time) instead of retiring the whole card on first click.
    """
    if not isinstance(card_json, str):
        return _core._error("card_json must be a JSON string containing an object")
    try:
        card = json.loads(card_json)
    except ValueError as exc:
        return _core._error(f"card_json is not valid JSON: {exc}")
    if not isinstance(card, dict):
        return _core._error(
            "card_json must be a JSON object — the Feishu card, e.g. "
            '{"schema":"2.0","body":{"elements":[...]}} or {"config":...,"elements":[...]}.'
        )
    if not isinstance(business_context_json, str):
        return _core._error("business_context_json must be a JSON string containing an object")
    try:
        business_context = json.loads(business_context_json)
    except ValueError as exc:
        return _core._error(f"business_context_json is not valid JSON: {exc}")
    if not isinstance(business_context, dict):
        return _core._error("business_context_json must be a JSON object")
    if not isinstance(action_handlers_json, str):
        return _core._error("action_handlers_json must be a JSON string containing an object")
    try:
        raw_action_handlers = json.loads(action_handlers_json)
    except ValueError as exc:
        return _core._error(f"action_handlers_json is not valid JSON: {exc}")
    if not isinstance(raw_action_handlers, dict):
        return _core._error("action_handlers_json must be a JSON object")
    if not all(
        isinstance(action_id, str)
        and bool(action_id)
        and action_id.strip() == action_id
        and isinstance(handler, str)
        and bool(handler)
        and handler.strip() == handler
        for action_id, handler in raw_action_handlers.items()
    ):
        return _core._error(
            "action_handlers_json keys and values must be non-empty strings without surrounding whitespace"
        )
    action_handlers = dict(raw_action_handlers)
    receive_id_type = _infer_receive_id_type(receive_id, receive_id_type)
    content = json.dumps(card, ensure_ascii=False)
    req = _build_send_message_request(receive_id, receive_id_type, "interactive", content)
    res = await _core._invoke(req, user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    message_id = data.get("message_id", "")
    if isinstance(message_id, str) and message_id:
        try:
            source = {
                "session_id": get_session_id().strip(),
                "sender_open_id": (user_key or "").strip(),
                "receive_id": receive_id,
                "receive_id_type": receive_id_type,
            }
            await save_card_snapshot(
                message_id,
                card,
                source=source,
                business_context=business_context,
                action_handlers=action_handlers,
                multi_use=multi_use,
            )
        except Exception as exc:
            logger.warning(f"failed to save Feishu card snapshot for {message_id} — {exc!r}")
            return _core._error(
                "Feishu card was sent, but its callback context could not be saved; card actions will fail closed.",
                sent=True,
                callback_context_saved=False,
                message_id=message_id,
                thread_id=data.get("thread_id", ""),
                chat_id=data.get("chat_id", ""),
            )
    return {
        "ok": True,
        "callback_context_saved": bool(message_id),
        "message_id": message_id,
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
    }


# ── Recall (unsend) a message ─────────────────────────────────────────────────
#
# DELETE /open-apis/im/v1/messages/:message_id removes a message from everyone's
# view. The bot can always recall what the bot itself sent (tenant token); recalling
# *someone else's* message additionally requires acting as a group owner/admin, i.e.
# that person's UAT — hence the tenant-first, UAT-on-permission-failure strategy.
# Messages sent through the batch-send API need the separate batch-recall endpoint.

_RECALL_ERROR_HINTS = {
    230002: "机器人不在该群里, 先把机器人加入群再撤回。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230009: "该消息已超出可撤回时限 (受企业管理员的撤回时限设置约束)。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230026: "只能撤回机器人自己发的消息; 撤回别人的消息需以群主/管理员身份操作 (传该管理员的 user_key 并完成授权)。",
    230027: "缺少撤回所需权限 (im:message 或 im:message:recall), 外部群还需开启对外共享。",
    230050: "该消息对当前操作身份不可见, 无法撤回。",
    230054: "该消息类型不支持撤回。",
    230110: "该消息已被撤回或删除, 无需再撤回。",
    232009: "群组已解散, 无法撤回。",
}


# ── Edit a message that was already sent ──────────────────────────────────────
#
# PUT /open-apis/im/v1/messages/:message_id replaces a sent message's content in
# place: the bubble keeps its id, its position in the chat and its thread, and
# Feishu just marks it 已编辑. That is the difference from recall+resend, which
# loses the id (breaking replies/threads that point at it) and shows everyone a
# "撤回了一条消息" notice.
#
# Only text and post messages can be edited this way. An interactive card is
# updated through PATCH on the same path (``edit_card_impl`` below); image / file /
# audio / media messages cannot be edited at all (230054) and do have to be
# recalled and re-sent.
#
# Three limits are invisible in the raw error text and are the ones editing
# actually trips over: only the *sender* may edit (230071), a message can be
# edited at most 20 times (230072), and the tenant admin configures how long a
# message stays editable (230075).

_EDIT_ERROR_HINTS = {
    230001: "请求参数不合法; 编辑只支持文本(text)和富文本(post)消息, 卡片要用 feishu_message_edit_card。",
    230002: "机器人不在该群里, 先把机器人加入群再编辑。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230011: "该消息已被撤回, 无法再编辑。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230018: "该群的设置不允许这次操作 (如全员禁言)。",
    230025: "内容超长 (文本上限约 150KB, 富文本约 30KB), 缩短后再编辑。",
    230027: "缺少编辑所需权限 (im:message / im:message:send_as_bot / im:message:update)。",
    230054: "该消息类型不支持编辑; 图片/文件/音频/视频消息只能撤回重发, 卡片用 feishu_message_edit_card。",
    230071: "只有消息的发送者能编辑它: 这条不是当前身份发的。机器人只能改自己发的消息; "
    "要改某人自己发的消息, 传该用户的 user_key 并让其完成授权。",
    230072: "该消息已达到 20 次编辑上限, 无法继续编辑。",
    230073: "密聊消息不支持编辑。",
    230074: "第三方加密群的消息不支持编辑。",
    230075: "已超出可编辑时限 (受企业管理员配置约束), 只能撤回重发。",
    230110: "该消息已被删除, 无法编辑。",
    232009: "群组已解散, 无法编辑。",
}


def _build_edit_message_request(message_id: str, msg_type: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"msg_type": msg_type, "content": content}
    return req


def _require_message_id(message_id: str, what: str) -> tuple[str, dict[str, Any] | None]:
    """Normalize an ``om_...`` message id, or explain why the given value can't be one."""
    mid = message_id.strip()
    if not mid:
        return "", _core._error(f"message_id is required (the om_... id of the message to {what}).")
    if not mid.startswith("om_"):
        return "", _core._error(
            f"message_id must be a message id starting with 'om_', got {mid!r}. "
            "chat_id (oc_...) / open_id (ou_...) 不是消息 id; "
            "消息 id 来自 feishu_message_send 的返回、<feishu_context>, "
            "或 feishu_api 调 GET /open-apis/im/v1/messages 列消息 (见 feishu-message 技能)。",
        )
    return mid, None


def _with_hint(res: dict[str, Any], hints: dict[int, str]) -> dict[str, Any]:
    """Attach the human-readable cause for a known Feishu error code, if we have one."""
    hint = hints.get(res.get("code"))  # type: ignore[arg-type]
    return {**res, "hint": hint} if hint else res


async def edit_message_impl(message_id: str, text: str, user_key: str = "") -> dict[str, Any]:
    """Replace the content of an already-sent text/post message, keeping its message_id.

    ``<at>`` tags in *text* are turned into a real ``post`` mention exactly as in
    ``send_message_impl`` — a plain-text ``<at>`` renders as a raw tag — so editing a
    message to add or fix a mention works.

    Tenant-first with a UAT fallback: the bot edits its own messages with its own
    token, and passing the sender's ``user_key`` is what makes editing *that person's*
    own message possible (Feishu only lets the sender edit).
    """
    mid, bad = _require_message_id(message_id, "edit")
    if bad is not None:
        return bad
    if not text.strip():
        return _core._error(
            "text is required: editing replaces the whole message content, and Feishu has no empty message. "
            "要让消息消失请撤回 (feishu_api 调 DELETE /open-apis/im/v1/messages/:message_id)。"
        )
    stripped, at_open_ids = _extract_and_strip_at_tags(text)
    if at_open_ids:
        # Mentions only render in a post message; a plain-text <at> shows the raw tag.
        msg_type = "post"
        content = _build_post_at_content(stripped, at_open_ids, at_all=False)
    else:
        msg_type = "text"
        content = json.dumps({"text": text}, ensure_ascii=False)
    res = await _core._invoke(_build_edit_message_request(mid, msg_type, content), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _EDIT_ERROR_HINTS)
    return {"ok": True, "message_id": mid, "edited": True, "msg_type": msg_type}


# A card is not edited by the text/post PUT above — it has its own PATCH on the same
# path, taking only ``content`` (the whole new card). Two extra rules apply:
# the card must declare ``config.update_multi`` (both the old and the new card;
# without it Feishu refuses or updates the card for only one viewer), and a card is
# only updatable for 14 days after it was sent.
_CARD_EDIT_ERROR_HINTS = {
    230001: "请求参数不合法; 这个接口只能更新**交互卡片**消息, 文本/富文本消息用 feishu_message_edit。",
    230011: "该卡片消息已被撤回, 无法再更新。",
    230025: "卡片超长 (上限约 30KB), 精简后再更新。",
    230027: "缺少更新所需权限 (im:message / im:message:send_as_bot / im:message:update)。",
    230054: "该消息不是交互卡片, 不支持卡片更新; 文本/富文本用 feishu_message_edit。",
    230071: "只有卡片的发送者能更新它: 这条不是当前身份发的。",
    230075: "已超出可更新时限 (卡片发送 14 天内可更新)。",
    230110: "该消息已被删除, 无法更新。",
    232009: "群组已解散, 无法更新。",
}


def _build_edit_card_request(message_id: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"content": content}
    return req


def _ensure_update_multi(card: dict[str, Any]) -> dict[str, Any]:
    """Make a legacy card updatable-for-everyone, which Feishu requires opt-in for.

    A legacy card without ``config.update_multi = true`` either refuses the update or
    applies it for a single viewer only — a silently half-broken result. Card 2.0
    (``{"schema": "2.0", ...}``) has no such flag and is left alone.
    """
    if str(card.get("schema", "")).startswith("2"):
        return card
    config = card.get("config")
    merged = dict(config) if isinstance(config, dict) else {}
    merged["update_multi"] = True
    return {**card, "config": merged}


async def edit_card_impl(message_id: str, card_json: str, user_key: str = "") -> dict[str, Any]:
    """Replace a sent interactive card's content in place, keeping its message_id.

    Its own endpoint (PATCH, not the text/post PUT) and its own payload: just the whole
    new card. Used to reflect state on a card that is already in the chat — mark an
    approval 已通过, disable buttons, refresh a dashboard — without the recipient losing
    the original bubble.

    The card's callback context is **not** re-registered: an already-sent card's
    handlers were snapshotted at send time and consumed on first click, so an update
    changes what the card *shows*, not what its buttons dispatch. Send a new card with
    ``send_card_impl`` when the actions themselves must change.
    """
    mid, bad = _require_message_id(message_id, "update")
    if bad is not None:
        return bad
    if not isinstance(card_json, str):
        return _core._error("card_json must be a JSON string containing an object")
    try:
        card = json.loads(card_json)
    except ValueError as exc:
        return _core._error(f"card_json is not valid JSON: {exc}")
    if not isinstance(card, dict):
        return _core._error(
            "card_json must be a JSON object — the full replacement card, e.g. "
            '{"schema":"2.0","body":{"elements":[...]}} or {"config":...,"elements":[...]}.'
        )
    content = json.dumps(_ensure_update_multi(card), ensure_ascii=False)
    res = await _core._invoke(_build_edit_card_request(mid, content), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _CARD_EDIT_ERROR_HINTS)
    return {"ok": True, "message_id": mid, "edited": True, "msg_type": "interactive"}


# ── Emoji reactions on a message ───────────────────────────────────────────────
#
# A reaction is the lightest possible acknowledgement: 收到 / 已处理 / 赞 without
# adding a message to the chat. Three endpoints under
# im/v1/messages/:message_id/reactions — POST to add (returns a reaction_id), DELETE
# .../:reaction_id to remove, GET to list.
#
# Removal needs the reaction_id, and only the identity that added a reaction can
# remove it. Rather than make the caller carry ids around, ``remove_reaction_impl``
# accepts an ``emoji_type`` and resolves it through the list endpoint, keeping the
# tool symmetric with add (same argument removes what it added).
#
# ``emoji_type`` values come from Feishu's emoji table and are **case-sensitive and
# inconsistently cased** (``THUMBSUP``/``OK``/``DONE`` but ``Fire``/``OnIt``/``Get``),
# so a wrong guess yields 231001. The common ones are aliased below.
_REACTION_ERROR_HINTS = {
    230110: "该消息已被删除, 无法操作表情回应。",
    231001: "emoji_type 不是飞书支持的值 (大小写敏感, 如 THUMBSUP / OK / DONE / Fire); 换一个再试。",
    231002: "当前身份不在该消息所在会话里, 先把机器人加入群 (或换成群内成员的 user_key)。",
    231003: "找不到该消息 (id 有误或已撤回)。",
    231004: "该会话不存在、已解散或已归档。",
    231008: "当前身份无权访问该消息。",
    231017: "该消息类型不支持表情回应 (如系统消息)。",
    231018: "该消息对当前身份不可见。",
    231021: "外部群里没有操作表情回应的权限。",
    231022: "机器人对该用户不可用 (把该用户加入应用可用范围后重新发布)。",
    232009: "群组已解散, 无法操作表情回应。",
}

# 中文/口语说法 → 飞书 emoji_type。飞书的枚举大小写混乱 (THUMBSUP 全大写, Fire 首字母大写),
# 模型按字面猜十次错九次, 所以常用的这些一律先过一遍映射, 并且大小写不敏感地兜住。
_EMOJI_ALIASES = {
    "赞": "THUMBSUP",
    "点赞": "THUMBSUP",
    "👍": "THUMBSUP",
    "好的": "OK",
    "ok": "OK",
    "👌": "OK",
    "完成": "DONE",
    "已完成": "DONE",
    "收到": "OnIt",
    "在办": "OnIt",
    "处理中": "OnIt",
    "感谢": "THANKS",
    "谢谢": "THANKS",
    "鼓掌": "APPLAUSE",
    "👏": "APPLAUSE",
    "笑": "SMILE",
    "😄": "SMILE",
    "心": "HEART",
    "❤️": "HEART",
    "爱心": "HEART",
    "火": "Fire",
    "🔥": "Fire",
    "庆祝": "PARTY",
    "🎉": "PARTY",
    "加油": "JIAYI",
    "对勾": "CheckMark",
    "✅": "DONE",
    "打勾": "CheckMark",
    "叉": "CrossMark",
    "❌": "CrossMark",
}
# The canonical spelling for values whose casing is the usual mistake, keyed lowercase.
_EMOJI_CANONICAL = {
    v.lower(): v
    for v in (
        "THUMBSUP",
        "OK",
        "DONE",
        "SMILE",
        "HEART",
        "APPLAUSE",
        "CLAP",
        "PRAISE",
        "THANKS",
        "LGTM",
        "Fire",
        "PARTY",
        "OnIt",
        "JIAYI",
        "Get",
        "CheckMark",
        "CrossMark",
        "Hundred",
        "Trophy",
        "FIREWORKS",
        "ROSE",
        "MUSCLE",
        "WAVE",
        "LAUGH",
        "CRY",
        "THINKING",
        "ThumbsDown",
        "MinusOne",
    )
}


def _normalize_emoji_type(emoji_type: str) -> str:
    """Map a Chinese word / emoji character / mis-cased key onto a Feishu ``emoji_type``.

    Unknown values pass through untouched: Feishu's table is ~130 entries and grows,
    so an unrecognized value is sent as given (and answered with 231001) rather than
    rejected here by a list that would go stale.
    """
    raw = emoji_type.strip()
    if not raw:
        return ""
    alias = _EMOJI_ALIASES.get(raw) or _EMOJI_ALIASES.get(raw.lower())
    if alias:
        return alias
    return _EMOJI_CANONICAL.get(raw.lower(), raw)


def _build_remove_reaction_request(message_id: str, reaction_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/im/v1/messages/:message_id/reactions/:reaction_id"
    req.paths["message_id"] = message_id
    req.paths["reaction_id"] = reaction_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_list_reactions_request(message_id: str, emoji_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id/reactions"
    req.paths["message_id"] = message_id
    if emoji_type:
        req.add_query("reaction_type", emoji_type)
    req.add_query("page_size", max(1, min(page_size, 50)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _reaction_record(item: Any) -> dict[str, Any]:
    """One reaction as {reaction_id, emoji_type, operator_id, operator_type, action_time}."""
    if not isinstance(item, dict):
        return {}
    reaction_type = item.get("reaction_type")
    operator = item.get("operator")
    return {
        "reaction_id": item.get("reaction_id", ""),
        "emoji_type": (reaction_type or {}).get("emoji_type", "") if isinstance(reaction_type, dict) else "",
        "operator_id": (operator or {}).get("operator_id", "") if isinstance(operator, dict) else "",
        "operator_type": (operator or {}).get("operator_type", "") if isinstance(operator, dict) else "",
        "action_time": item.get("action_time", ""),
    }


async def list_reactions_impl(
    message_id: str, emoji_type: str = "", page_size: int = 50, page_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """List a message's reactions — who reacted with what, and each ``reaction_id``."""
    mid, bad = _require_message_id(message_id, "list reactions of")
    if bad is not None:
        return bad
    emoji = _normalize_emoji_type(emoji_type)
    res = await _core._invoke(
        _build_list_reactions_request(mid, emoji, page_size, page_token.strip()),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _REACTION_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw_items = data.get("items")
    items: list[Any] = raw_items if isinstance(raw_items, list) else []
    reactions = [r for r in (_reaction_record(i) for i in items) if r]
    return {
        "ok": True,
        "message_id": mid,
        "reactions": reactions,
        "count": len(reactions),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


async def remove_reaction_impl(
    message_id: str, emoji_type: str = "", reaction_id: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Remove a reaction, addressed either by ``reaction_id`` or by its emoji.

    Feishu deletes by ``reaction_id`` and only lets the identity that added a reaction
    remove it. Given an ``emoji_type`` instead, the message's reactions are listed and
    the matching one is resolved — so "把刚才那个赞取消" works from the same argument
    that added it, without the caller having stored an id.

    Resolution stays deliberately strict: if several reactions share that emoji (added
    by different people), the ids are returned and nothing is deleted rather than
    guessing whose to take back.
    """
    mid, bad = _require_message_id(message_id, "remove a reaction from")
    if bad is not None:
        return bad
    rid = reaction_id.strip()
    emoji = _normalize_emoji_type(emoji_type)
    if not rid:
        if not emoji:
            return _core._error("pass either reaction_id, or emoji_type (e.g. THUMBSUP / 赞) to look it up.")
        listed = await list_reactions_impl(mid, emoji, page_size=50, user_key=user_key)
        if not listed["ok"]:
            return listed
        matches = [r for r in listed["reactions"] if r["emoji_type"] == emoji and r["reaction_id"]]
        if not matches:
            return _core._error(
                f"没有找到 emoji_type={emoji!r} 的表情回应 (可能本来没加, 或已被取消)。",
                message_id=mid,
                emoji_type=emoji,
                code="reaction_not_found",
            )
        if len(matches) > 1:
            return _core._error(
                f"该消息上有 {len(matches)} 个 {emoji!r} 表情回应 (不同人加的), 无法确定要取消哪一个; "
                "从 candidates 里挑一个 reaction_id 再调一次 (只能取消自己加的那个)。",
                message_id=mid,
                emoji_type=emoji,
                candidates=matches,
                code="reaction_ambiguous",
            )
        rid = matches[0]["reaction_id"]
    res = await _core._invoke(_build_remove_reaction_request(mid, rid), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _REACTION_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    # The echoed record first, then the ids we know: Feishu's delete response omits
    # fields for some message types, and an empty echo must not blank out the answer.
    return {"ok": True, **_reaction_record(data), "message_id": mid, "reaction_id": rid, "removed": True}


# ── Rich media messages — image / file / audio / video / rich text ──────────────
#
# Sending anything but text is always two calls: upload the bytes to get a key, then
# send a message whose content references that key. Two *different* upload endpoints,
# and picking the wrong one is the usual failure:
#
#   im/v1/images  → image_key (img_v3_...)  — pictures only, ≤10MB
#   im/v1/files   → file_key  (file_v3_...) — documents, audio, video, ≤30MB
#
# These are IM-message uploads, unrelated to drive medias/upload_all (which puts a
# file in the cloud drive / a doc block, see upload_media_impl). A drive file_token
# cannot be sent as a message and vice versa.
#
# Both go out as multipart, which under this SDK means the binary must sit in the
# request **body** as an io.IOBase carrying a .name — Client.arequest overwrites
# req.files with Files.extract_files(req.body) right before sending, so a file put in
# req.files is dropped and the request leaves as application/json ("boundary not
# found"). Same reason _NamedBytes exists for drive uploads.
_IMAGE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
_FILE_UPLOAD_MAX_BYTES = 30 * 1024 * 1024

# What Feishu accepts for im/v1/images. TIFF/HEIC are converted to JPG server-side.
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".ico", ".tif", ".tiff", ".heic"}
# file_type for im/v1/files is an enum, not the extension: audio must be opus, video
# mp4, documents their own four, and anything else falls back to "stream" (which is
# what a .zip/.csv/.txt attachment is sent as).
_FILE_TYPE_BY_SUFFIX = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
_FILE_TYPES = {"opus", "mp4", "pdf", "doc", "xls", "ppt", "stream"}
# msg_type → which upload endpoint feeds it, so one send path serves all of them.
_MEDIA_MSG_TYPES = {"image": "image", "file": "file", "audio": "file", "media": "file"}
_UPLOAD_ERROR_HINTS = {
    234001: "上传参数不合法 (image_type / file_type / file_name 有问题)。",
    234002: "上传鉴权失败, 检查 PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET。",
    234006: "文件超过大小上限 (图片 10MB, 文件 30MB)。",
    234007: "应用未启用机器人能力, 到开发者后台开启后再试。",
    234010: "文件是空的 (0 字节), 飞书拒收。",
    234011: "无法识别的图片格式; 支持 JPG/JPEG/PNG/WEBP/GIF/BMP/ICO/TIFF/HEIC。",
    234039: "图片分辨率超限 (GIF 2000x2000, 其它 12000x12000); 改用文件方式发送。",
}
_SEND_MEDIA_ERROR_HINTS = {
    230001: "请求参数不合法; 常见原因是 image_key 与 file_key 用反了 (图片用 image_key, 音视频/文件用 file_key)。",
    230002: "机器人不在该群里, 先把机器人加入群。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230055: "上传时的 file_type 与消息类型不一致 (音频要 opus, 视频要 mp4)。",
}


def _build_image_upload_request(image_type: str, file_name: str, data: bytes) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/images"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # Binary in the body (not req.files) — see the note above _IMAGE_UPLOAD_MAX_BYTES.
    req.body = {"image_type": image_type, "image": _core._NamedBytes(data, file_name)}
    return req


def _build_file_upload_request(file_type: str, file_name: str, data: bytes, duration_ms: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/files"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"file_type": file_type, "file_name": file_name}
    if duration_ms > 0:
        body["duration"] = duration_ms
    body["file"] = _core._NamedBytes(data, file_name)
    req.body = body
    return req


async def _read_upload_bytes(file_path: str, limit: int, what: str) -> tuple[bytes, str, dict[str, Any] | None]:
    """Read a local file for upload; returns (data, name, error) with error set on refusal."""
    p = anyio.Path(file_path)
    if not await p.is_file():
        return b"", "", _core._error(f"file not found: {file_path}")
    data = await p.read_bytes()
    if not data:
        return b"", "", _core._error(f"{file_path} is empty (0 bytes); Feishu rejects empty uploads.")
    if len(data) > limit:
        return (
            b"",
            "",
            _core._error(
                f"{what} is {len(data)} bytes, over the {limit // (1024 * 1024)}MB limit for this endpoint. "
                "更大的文件先上传到云盘 (feishu_drive_upload) 再把链接发出去。",
                size=len(data),
            ),
        )
    return data, p.name, None


async def upload_image_impl(image_path: str, user_key: str = "") -> dict[str, Any]:
    """Upload a picture for use in messages; returns its ``image_key`` (``img_v3_...``).

    Separate from ``upload_media_impl`` (cloud drive): only an IM ``image_key`` can be
    sent as an image message or embedded in a post, and only a drive ``file_token``
    can live in a document.
    """
    data, name, bad = await _read_upload_bytes(image_path, _core._IMAGE_UPLOAD_MAX_BYTES, "image")
    if bad is not None:
        return bad
    suffix = pathlib.Path(name).suffix.lower()
    if suffix and suffix not in _IMAGE_SUFFIXES:
        return _core._error(
            f"{name} is not an image Feishu accepts ({', '.join(sorted(_IMAGE_SUFFIXES))}). "
            "非图片文件用 feishu_message_send_file 发送。",
        )
    # A factory: the SDK consumes the file entry on the first send, and this may be
    # retried under a second identity.
    res = await _core._invoke(
        lambda: _build_image_upload_request("message", name, data),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _UPLOAD_ERROR_HINTS)
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "image_key": rdata.get("image_key", ""), "file_name": name, "size": len(data)}


async def upload_file_impl(
    file_path: str, file_type: str = "", file_name: str = "", duration_ms: int = 0, user_key: str = ""
) -> dict[str, Any]:
    """Upload a document/audio/video for use in messages; returns its ``file_key``.

    ``file_type`` is Feishu's enum, not the extension — it is derived from the suffix
    (``.mp4``→mp4, ``.pdf``→pdf, ``.docx``→doc, …) and anything unmapped uploads as
    ``stream``, which is how a .zip/.csv/.txt attachment is sent.

    Audio must genuinely be OPUS: Feishu plays an ``audio`` message only for
    ``file_type=opus``, and sending an .mp3 as audio is rejected with 230055. Convert
    first (``ffmpeg -i in.mp3 -acodec libopus -ac 1 -ar 16000 out.opus``) or send the
    .mp3 as a plain file instead.
    """
    data, name, bad = await _read_upload_bytes(file_path, _FILE_UPLOAD_MAX_BYTES, "file")
    if bad is not None:
        return bad
    name = file_name.strip() or name
    ftype = file_type.strip() or _FILE_TYPE_BY_SUFFIX.get(pathlib.Path(name).suffix.lower(), "stream")
    if ftype not in _FILE_TYPES:
        return _core._error(
            f"file_type must be one of {', '.join(sorted(_FILE_TYPES))}, got {ftype!r} "
            "(it is Feishu's enum, not the file extension; unlisted formats use 'stream').",
        )
    res = await _core._invoke(
        lambda: _build_file_upload_request(ftype, name, data, max(0, duration_ms)),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _UPLOAD_ERROR_HINTS)
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "file_key": rdata.get("file_key", ""), "file_name": name, "file_type": ftype, "size": len(data)}


async def send_media_message_impl(
    receive_id: str,
    file_path: str,
    msg_type: str,
    receive_id_type: str = "chat_id",
    cover_image_path: str = "",
    file_name: str = "",
    duration_ms: int = 0,
    user_key: str = "",
) -> dict[str, Any]:
    """Upload a local file and send it as an image / file / audio / video message.

    Both halves of the two-call dance in one place, because doing them separately is
    where the keys get crossed: ``msg_type`` decides which upload endpoint runs
    (``image`` → im/v1/images → ``image_key``; everything else → im/v1/files →
    ``file_key``) and what the message content looks like.

    ``media`` (video) may carry a cover: ``cover_image_path`` is uploaded as an image
    and referenced as the thumbnail. Without one the video shows no preview frame.
    """
    kind = msg_type.strip().lower()
    if kind not in _MEDIA_MSG_TYPES:
        return _core._error(
            f"msg_type must be one of {', '.join(sorted(_MEDIA_MSG_TYPES))}, got {msg_type!r}. "
            "image=图片, file=文档/附件, audio=语音(opus), media=视频(mp4)。",
        )
    if kind == "image":
        uploaded = await upload_image_impl(file_path, user_key=user_key)
        if not uploaded["ok"]:
            return uploaded
        content: dict[str, Any] = {"image_key": uploaded["image_key"]}
        detail = {"image_key": uploaded["image_key"]}
    else:
        forced = {"audio": "opus", "media": "mp4"}.get(kind, "")
        uploaded = await _core.upload_file_impl(
            file_path, file_type=forced, file_name=file_name, duration_ms=duration_ms, user_key=user_key
        )
        if not uploaded["ok"]:
            return uploaded
        content = {"file_key": uploaded["file_key"]}
        detail = {"file_key": uploaded["file_key"], "file_type": uploaded["file_type"]}
        if kind == "media" and cover_image_path.strip():
            cover = await upload_image_impl(cover_image_path.strip(), user_key=user_key)
            if not cover["ok"]:
                # The video is uploaded and sendable; a missing cover must not lose it.
                logger.warning(f"video cover upload failed, sending without a cover — {cover.get('message', '')}")
            else:
                content["image_key"] = cover["image_key"]
                detail["cover_image_key"] = cover["image_key"]
    rid_type = _infer_receive_id_type(receive_id, receive_id_type)
    req = _build_send_message_request(receive_id, rid_type, kind, json.dumps(content, ensure_ascii=False))
    res = await _core._invoke(req, user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, **detail}, _SEND_MEDIA_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
        "msg_type": kind,
        "size": uploaded["size"],
        **detail,
    }


# A post message is the only way to put text, pictures, links and mentions in **one**
# bubble. Its content is a list of paragraphs, each a list of nodes — so the tool takes
# a compact block list and expands it, uploading any local image on the way. Feishu
# requires img and media nodes to occupy a paragraph of their own, which the builder
# enforces rather than leaving to the caller.
_POST_BLOCK_TAGS = {"text", "a", "at", "img", "code_block", "hr", "md"}


def _post_node(block: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """One post node from a compact block dict; returns (node, error message)."""
    tag = str(block.get("tag", "text")).strip() or "text"
    if tag not in _POST_BLOCK_TAGS:
        return None, f"unsupported tag {tag!r}; use one of {', '.join(sorted(_POST_BLOCK_TAGS))}"
    if tag == "hr":
        return {"tag": "hr"}, ""
    if tag == "at":
        user_id = str(block.get("user_id", "")).strip()
        if not user_id:
            return None, "an 'at' block needs user_id (an ou_... open_id, or \"all\")"
        return {"tag": "at", "user_id": user_id}, ""
    if tag == "a":
        href = str(block.get("href", "")).strip()
        if not href:
            return None, "an 'a' block needs href"
        return {"tag": "a", "text": str(block.get("text", "")) or href, "href": href}, ""
    if tag == "img":
        # image_path is resolved to an image_key by the caller before we get here.
        image_key = str(block.get("image_key", "")).strip()
        if not image_key:
            return None, "an 'img' block needs image_key or image_path"
        return {"tag": "img", "image_key": image_key}, ""
    text = block.get("text")
    if not isinstance(text, str) or not text:
        return None, f"a {tag!r} block needs non-empty text"
    if tag == "code_block":
        node: dict[str, Any] = {"tag": "code_block", "text": text}
        language = str(block.get("language", "")).strip()
        if language:
            node["language"] = language
        return node, ""
    if tag == "md":
        return {"tag": "md", "text": text}, ""
    node = {"tag": "text", "text": text}
    style = block.get("style")
    if isinstance(style, list) and style:
        node["style"] = [str(s) for s in style]
    return node, ""


def _build_post_content(title: str, nodes: list[dict[str, Any]]) -> str:
    """Group post nodes into paragraphs: img/hr/md stand alone, runs of text merge."""
    paragraphs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for node in nodes:
        if node["tag"] in {"img", "hr", "md"}:
            if current:
                paragraphs.append(current)
                current = []
            paragraphs.append([node])
            continue
        current.append(node)
    if current:
        paragraphs.append(current)
    return json.dumps({"zh_cn": {"title": title, "content": paragraphs}}, ensure_ascii=False)


async def send_post_message_impl(
    receive_id: str,
    blocks_json: str,
    title: str = "",
    receive_id_type: str = "chat_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Send a **rich text** (post) message: styled text, links, mentions and images in one bubble.

    ``blocks_json`` is a JSON array of compact blocks, in order, e.g.::

        [{"tag": "text", "text": "本周周报", "style": ["bold"]},
         {"tag": "at", "user_id": "ou_xxx"},
         {"tag": "a", "text": "看板", "href": "https://..."},
         {"tag": "img", "image_path": "C:/tmp/chart.png"},
         {"tag": "md", "text": "1. 第一项\\n2. 第二项"}]

    An ``img`` block may name a local ``image_path`` (uploaded here) or an existing
    ``image_key``. Blocks are grouped into paragraphs the way Feishu requires — images,
    separators and markdown each get their own line, adjacent text/link/mention nodes
    share one — so the caller writes a flat list and gets a correct layout.
    """
    if not isinstance(blocks_json, str):
        return _core._error("blocks_json must be a JSON string containing an array of blocks")
    try:
        blocks = json.loads(blocks_json)
    except ValueError as exc:
        return _core._error(f"blocks_json is not valid JSON: {exc}")
    if not isinstance(blocks, list) or not blocks:
        return _core._error(
            'blocks_json must be a non-empty JSON array, e.g. [{"tag":"text","text":"hi"},'
            '{"tag":"img","image_path":"C:/tmp/a.png"}]'
        )
    nodes: list[dict[str, Any]] = []
    uploaded_keys: list[str] = []
    for position, raw_block in enumerate(blocks):
        if not isinstance(raw_block, dict):
            return _core._error(f"block #{position} is not a JSON object", block_index=position)
        block: dict[str, Any] = {str(k): v for k, v in raw_block.items()}
        if str(block.get("tag", "")).strip() == "img" and not str(block.get("image_key", "")).strip():
            path = str(block.get("image_path", "")).strip()
            if not path:
                return _core._error(
                    f"block #{position}: an 'img' block needs image_path or image_key", block_index=position
                )
            up = await upload_image_impl(path, user_key=user_key)
            if not up["ok"]:
                return {**up, "block_index": position}
            block["image_key"] = up["image_key"]
            uploaded_keys.append(up["image_key"])
        node, err = _post_node(block)
        if node is None:
            return _core._error(f"block #{position}: {err}", block_index=position)
        nodes.append(node)
    rid_type = _infer_receive_id_type(receive_id, receive_id_type)
    content = _build_post_content(title.strip(), nodes)
    res = await _core._invoke(
        _build_send_message_request(receive_id, rid_type, "post", content), user_key=user_key, prefer="tenant"
    )
    if not res["ok"]:
        return _with_hint(res, _SEND_MEDIA_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
        "msg_type": "post",
        "blocks": len(nodes),
        "uploaded_image_keys": uploaded_keys,
    }


def _build_list_messages_request(
    container_id: str, container_id_type: str, sort_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("container_id_type", container_id_type)
    req.add_query("container_id", container_id)
    req.add_query("sort_type", sort_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _extract_post_text(node: Any) -> str:
    """Recursively collect all 'text' values from a post rich-text content tree."""
    parts: list[str] = []
    if isinstance(node, dict):
        if node.get("tag") == "text" and isinstance(node.get("text"), str):
            parts.append(node["text"])
        for v in node.values():
            if isinstance(v, (dict, list)):
                parts.append(_extract_post_text(v))
    elif isinstance(node, list):
        for v in node:
            parts.append(_extract_post_text(v))
    return " ".join(p for p in parts if p)


def _message_plain_text(item: dict[str, Any]) -> str:
    """Best-effort plain text of a message item (handles text and post; others -> '')."""
    if item.get("deleted"):
        return ""
    body = item.get("body", {}) if isinstance(item.get("body"), dict) else {}
    raw = body.get("content", "")
    if not raw:
        return ""
    try:
        content = json.loads(raw)
    except ValueError, TypeError:
        return raw if isinstance(raw, str) else ""
    if not isinstance(content, dict):
        return ""
    if "text" in content and isinstance(content["text"], str):
        return content["text"]
    return _extract_post_text(content)  # post / rich text


async def read_thread_impl(thread_id: str, page_size: int = 50) -> dict[str, Any]:
    """Read a topic thread and return cleaned messages: [{message_id, sender_open_id, name?, text}]."""
    messages: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _core._invoke(
            _build_list_messages_request(thread_id, "thread", "ByCreateTimeAsc", page_size, page_token)
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            sender = it.get("sender", {}) if isinstance(it.get("sender"), dict) else {}
            is_user = sender.get("sender_type") == "user"
            messages.append(
                {
                    "message_id": it.get("message_id", ""),
                    "sender_open_id": sender.get("id", "") if is_user else "",
                    "sender_type": sender.get("sender_type", ""),
                    "create_time": it.get("create_time", ""),
                    "text": _message_plain_text(it),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "thread_id": thread_id, "messages": messages, "count": len(messages)}


# ── Contact — resolve a member's user id (open_id) by name via chat roster ────
#
# Feishu tenant tokens cannot search all users by name; the supported path is to
# list a group's members (each item has name + member_id) and match by name.
# This resolves the "@ a specific person" need — the target is a group member.


def _build_chat_members_request(chat_id: str, member_id_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/:chat_id/members"
    req.paths["chat_id"] = chat_id
    req.add_query("member_id_type", member_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def find_member_id_impl(
    chat_id: str,
    name: str,
    exact: bool,
    member_id_type: str = "open_id",
) -> dict[str, Any]:
    """Resolve a group member's id by name. Pages through the full roster and matches by name.

    Returns matches [{name, id, member_id_type}]. ``name`` empty returns the whole roster.
    """
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _core._invoke(_build_chat_members_request(chat_id, member_id_type, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "name": it.get("name", ""),
                    "id": it.get("member_id", ""),
                    "member_id_type": it.get("member_id_type", member_id_type),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    if not name:
        matches = members
    elif exact:
        matches = [m for m in members if m["name"] == name]
    else:
        matches = [m for m in members if name in m["name"]]
    return {
        "ok": True,
        "chat_id": chat_id,
        "query": name,
        "exact": exact,
        "matches": matches,
        "count": len(matches),
        "member_total": len(members),
    }


# ── Group administration — read a group's settings, add/remove members ───────
#
# ``create_chat_impl`` could only pull people in at creation time; running a group
# afterwards needs the roster to be editable and its settings to be readable. Both
# halves share Feishu's im/v1 chat errors, so the hint table is shared too. The
# 232017 case is the one that actually bites: most groups restrict 加人 to
# owner/admin, and the bot is neither unless it created the group — so the caller
# must pass that person's ``user_key`` rather than expect the bot to manage.
_CHAT_ADMIN_ERROR_HINTS = {
    232006: "chat_id 无效; 用 feishu_api 调 GET /open-apis/im/v1/chats/search 重解析。",
    232009: "群已解散, 无法操作。",
    232010: "机器人与该群不在同一租户 (外部群), 内部接口管不了。",
    232011: "机器人不在该群里, 先把机器人加入群。",
    232013: "群成员数已达上限 (普通群/话题群 5000, 会议群 3000)。",
    232014: "token 缺少所需权限 (im:chat 或 im:chat.members:write_only)。",
    232017: "该群限定「仅群主和群管理员可添加成员」, 机器人不是群主/管理员; "
    "传群主或管理员的 user_key 以本人身份操作, 或请他们把该设置改为「所有群成员」。",
    232019: "同一个群被并发操作触发限流; 串行调用重试。",
    232024: "机器人对该用户不可见, 或双方无协作权限; 检查应用可用范围。",
    232025: "应用未启用机器人能力, 到开发者后台开启后再试。",
    232027: "id_list 里没有有效成员。",
    232028: "外部成员不能加入内部群。",
    232033: "无操作外部群的权限。",
    232034: "应用在该租户未安装或未启用。",
    232043: "列表里含不可用的 ID; 核对后重试。",
    232044: "达到企业管理员配置的成员上限, 需管理员放开。",
    232076: "群主不能被移出群; 先转让群主再移出。",
    232090: "群类型不支持该操作 (仅普通群 group / 话题群 topic)。",
    99992351: "open_id 必须是 ou_ 前缀; 用 feishu_chat_find_member 或 GET /open-apis/search/v1/user 解析。",
}
# Feishu returns every group setting as a bare enum string. Naming them once here
# keeps the tool's answer readable ("只有群主能加人") instead of making the model
# guess what only_owner means in each of eight different fields.
_CHAT_WHO = {
    "only_owner": "仅群主和管理员",
    "all_members": "所有群成员",
    "not_anyone": "任何人都不可",
    "moderator_list": "指定人员",
    "allowed": "允许",
    "not_allowed": "不允许",
}
_CHAT_SETTING_FIELDS = (
    ("add_member_permission", "谁可以加人"),
    ("share_card_permission", "是否可分享群名片"),
    ("at_all_permission", "谁可以@所有人"),
    ("edit_permission", "谁可以编辑群信息"),
    ("membership_approval", "入群是否需审批"),
    ("moderation_permission", "谁可以发言"),
    ("join_message_visibility", "入群消息对谁可见"),
    ("leave_message_visibility", "退群消息对谁可见"),
    ("urgent_setting", "谁可以加急"),
    ("video_conference_setting", "谁可以发起视频会议"),
    ("hide_member_count_setting", "对谁隐藏成员数"),
)


def _build_get_chat_request(chat_id: str, user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/:chat_id"
    req.paths["chat_id"] = chat_id
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _chat_settings(data: dict[str, Any]) -> dict[str, str]:
    """Group settings as {人话标签: 人话取值}, skipping fields Feishu didn't return."""
    out: dict[str, str] = {}
    for key, label in _CHAT_SETTING_FIELDS:
        raw = data.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if key == "membership_approval":
            out[label] = "需审批" if raw == "approval_required" else "无需审批"
            continue
        out[label] = _CHAT_WHO.get(raw, raw)
    restricted = data.get("restricted_mode_setting")
    if isinstance(restricted, dict) and restricted.get("status"):
        out["保密模式"] = "已开启"
        for key, label in (
            ("screenshot_has_permission_setting", "可截屏录屏"),
            ("download_has_permission_setting", "可下载图片/视频/文件"),
            ("message_has_permission_setting", "可复制转发"),
        ):
            raw = restricted.get(key)
            if isinstance(raw, str) and raw:
                out[label] = _CHAT_WHO.get(raw, raw)
    return out


def _chat_details(cid: str, data: dict[str, Any], user_id_type: str) -> dict[str, Any]:
    """Shape one ``GET /chats/:chat_id`` payload into the tool's result."""
    owner_id = data.get("owner_id", "") or ""
    # user_count/bot_count come back as strings; a count is only useful as a number.
    counts: dict[str, Any] = {}
    for key in ("user_count", "bot_count"):
        raw = data.get(key)
        if isinstance(raw, str | int):
            with contextlib.suppress(TypeError, ValueError):
                counts[key] = int(raw)
    return {
        "ok": True,
        "chat_id": cid,
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "owner_id": owner_id,
        "owner_id_type": data.get("owner_id_type", "") or (user_id_type if owner_id else ""),
        "owner_is_bot": not owner_id and data.get("chat_mode") != "p2p",
        **counts,
        "user_manager_ids": data.get("user_manager_id_list") or [],
        "bot_manager_app_ids": data.get("bot_manager_id_list") or [],
        "chat_mode": data.get("chat_mode", ""),
        "chat_type": data.get("chat_type", ""),
        "chat_tag": data.get("chat_tag", ""),
        "chat_status": data.get("chat_status", ""),
        "external": bool(data.get("external")),
        "settings": _chat_settings(data),
        "avatar": data.get("avatar", ""),
        # A caller outside the group gets a stub; say so instead of implying the group
        # has no owner or no settings.
        "partial": not owner_id and not data.get("chat_mode"),
    }


async def get_chat_impl(chat_id: str, user_id_type: str = "open_id", user_key: str = "") -> dict[str, Any]:
    """Read a group's owner, member counts, and settings.

    Feishu deliberately answers a **non-member** caller with only name/avatar/counts
    /status, so a thin result is not an error — ``partial`` says so rather than letting
    the caller report "这个群没有群主". ``owner_id`` is also absent when the owner is a
    bot, which is why the two cases are distinguished in the result.

    "Non-member" is about *whose token asked*. The bot is not in most groups the user is
    in, so asking as the bot and reporting the stub reads as "this group is unreadable"
    when the person who asked is sitting in it. So a stub is retried as the caller when
    a ``user_key`` is available, and if it is still thin the result says what would make
    it complete rather than leaving the caller to guess.
    """
    cid = chat_id.strip()
    if not cid:
        return _core._error("chat_id is required (oc_...); resolve the group name first via feishu_api.")
    res = await _core._invoke(_build_get_chat_request(cid, user_id_type), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _CHAT_ADMIN_ERROR_HINTS)
    out = _chat_details(cid, res["data"] if isinstance(res["data"], dict) else {}, user_id_type)
    if out["partial"] and (user_key or "").strip():
        retry = await _core._invoke(
            _build_get_chat_request(cid, user_id_type), user_key=user_key, prefer="user", identity="user"
        )
        if retry["ok"]:
            asked_as_user = _chat_details(cid, retry["data"] if isinstance(retry["data"], dict) else {}, user_id_type)
            if not asked_as_user["partial"]:
                return {**asked_as_user, "asked_as": "user"}
    if out["partial"]:
        out["partial_because"] = (
            "机器人不在这个群里, 飞书只给了群名/头像/状态。成员数和群主看不到 —— "
            "user_count 是非成员视角的残缺值, 不代表群里真的没人。"
        )
        out["to_see_more"] = (
            "本人在群里就带上本人 user_key 以其身份重读(本工具会自动重试一次); "
            "本人也不在群里的话, 只能把机器人拉进群才看得到成员。"
        )
    return out


# ── 群公告 (chat announcement) — read and write the pinned notice board ──────────
#
# An announcement is not a message: it is a *document* hanging off the chat, so it is
# read and written with the docx block APIs (docx/v1/chats/:chat_id/announcement/...)
# rather than im/v1. That is the whole reason this needs a tool instead of one
# feishu_api call — writing one requires three separate facts to line up:
#
# 1. There are TWO generations of announcement. The legacy one (im/v1 .../announcement,
#    old-doc serialization) and the docx one. Feishu refuses cross-generation calls with
#    232097, and every group created in recent years is docx. So only the docx endpoints
#    are used here, and 232097 is translated rather than passed through as a bare code.
# 2. Every write is optimistic-locked on ``revision_id``. Sending a stale one fails, so
#    the revision is always read immediately before writing instead of being asked of
#    the caller — an agent has no way to know it.
# 3. The root block_id of an announcement is the ``chat_id`` itself (the same trick docx
#    uses, where document_id doubles as the root block_id). Guessing anything else here
#    produces a 404 that reads like "no announcement".
_ANNOUNCEMENT_ERROR_HINTS = {
    232001: "参数不合法; 检查 chat_id 是不是 oc_ 开头的群 (单聊 p2p 没有群公告)。",
    232002: "该群限定「仅群主和管理员可编辑群信息」; 传群主/管理员的 user_key 以本人身份改, 或请他们放开该设置。",
    232003: "群公告数据异常, 稍后重试。",
    232010: "操作者与该群不在同一租户 (外部群), 内部接口管不了。",
    232011: "调用者不在该群里; 先把机器人 (或本人) 加入群。",
    232018: "更新失败, 请求结构有问题 (检查 content 是否为空)。",
    232019: "同一个群被并发操作触发限流; 串行调用重试。",
    232024: "群公告可见性或协作权限不足。",
    232025: "应用未启用机器人能力, 到开发者后台开启后再试。",
    232033: "外部群不支持该操作。",
    232034: "应用在该租户未安装或未启用。",
    232066: "缺少群公告文档的阅读权限; 让群主把公告共享给机器人, 或传本人 user_key。",
    232097: "这是旧版 (非 docx) 群公告, 本工具的 docx 端点操作不了; "
    "请群主在群里手动把公告重建一次 (新建的即为 docx 版), 或改用旧版接口。",
}


def _build_announcement_get_request(chat_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement"
    req.paths["chat_id"] = chat_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_announcement_blocks_request(chat_id: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement/blocks"
    req.paths["chat_id"] = chat_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_announcement_children_request(
    chat_id: str, children: list[dict[str, Any]], revision_id: int, index: int
) -> BaseRequest:
    """Append blocks under the announcement root (whose block_id IS the chat_id)."""
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement/blocks/:block_id/children"
    req.paths["chat_id"] = chat_id
    req.paths["block_id"] = chat_id
    req.add_query("revision_id", revision_id)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"children": children}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


def _build_announcement_delete_request(chat_id: str, start: int, end: int, revision_id: int) -> BaseRequest:
    """Delete children [start, end) of the announcement root — the range is half-open."""
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement/blocks/:block_id/children/batch_delete"
    req.paths["chat_id"] = chat_id
    req.paths["block_id"] = chat_id
    req.add_query("revision_id", revision_id)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"start_index": start, "end_index": end}
    return req


async def _announcement_meta(chat_id: str, user_key: str) -> dict[str, Any]:
    """``{revision_id, announcement_type, ...}`` for a chat's announcement.

    Read before every write: ``revision_id`` is an optimistic lock the caller cannot
    know, and ``announcement_type`` tells us up front whether the docx endpoints even
    apply (a legacy announcement would otherwise fail mid-write with 232097).
    """
    res = await _core._invoke(_build_announcement_get_request(chat_id), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _ANNOUNCEMENT_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    revision = data.get("revision_id")
    if not isinstance(revision, int):
        with contextlib.suppress(TypeError, ValueError):
            revision = int(str(revision))
    return {
        "ok": True,
        "revision_id": revision if isinstance(revision, int) else 0,
        "announcement_type": data.get("announcement_type", "") or "",
        "owner_id": data.get("owner_id", "") or "",
        "modifier_id": data.get("modifier_id", "") or "",
        "create_time": data.get("create_time_v2") or data.get("create_time") or "",
        "update_time": data.get("update_time_v2") or data.get("update_time") or "",
    }


async def read_chat_announcement_impl(chat_id: str, max_chars: int = 20000, user_key: str = "") -> dict[str, Any]:
    """Read a group's 群公告 as plain text plus its block structure.

    The announcement is a document, so this pages its blocks and joins their text the
    same way ``list_doc_blocks_impl`` does — the caller wants to know what the notice
    says, not to parse docx JSON. ``blocks`` is returned alongside so a follow-up edit
    can address a specific paragraph.

    An **empty** announcement is a legitimate answer (``text == ""``, ``block_count``
    counting only the root), not an error: a group that never had a notice set still
    has an announcement document.
    """
    cid = chat_id.strip()
    if not cid:
        return _core._error("chat_id is required (oc_...); resolve the group name first via feishu_api.")
    meta = await _announcement_meta(cid, user_key)
    if not meta["ok"]:
        return meta

    limit = max(1, min(int(max_chars or 20000), 100000))
    blocks: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _core._invoke(
            _build_announcement_blocks_request(cid, _core._BLOCKS_LIST_PAGE_MAX, page_token),
            user_key=user_key,
            prefer="tenant",
        )
        if not res["ok"]:
            return _with_hint(res, _ANNOUNCEMENT_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            block_type = raw.get("block_type") or 0
            blocks.append(
                {
                    "block_id": raw.get("block_id", ""),
                    "block_type": block_type,
                    "type_name": _core._BLOCK_TYPE_NAMES.get(block_type, str(block_type)),
                    "parent_id": raw.get("parent_id", ""),
                    "text": _core._block_plain_text(raw),
                }
            )
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token:
            break

    # The root block (its id is the chat_id) is scaffolding, not content.
    body = [b for b in blocks if b["block_id"] != cid]
    text = "\n".join(b["text"] for b in body if b["text"])
    return {
        "ok": True,
        "chat_id": cid,
        "revision_id": meta["revision_id"],
        "announcement_type": meta["announcement_type"],
        "owner_id": meta["owner_id"],
        "modifier_id": meta["modifier_id"],
        "update_time": meta["update_time"],
        "text": text if len(text) <= limit else text[:limit] + "…",
        "truncated": len(text) > limit,
        "block_count": len(body),
        "blocks": body,
        "empty": not text.strip(),
    }


async def set_chat_announcement_impl(
    chat_id: str,
    content: str,
    replace: bool = True,
    user_key: str = "",
) -> dict[str, Any]:
    """Write a group's 群公告 from plain text / light Markdown headings.

    ``replace=True`` (the default) rewrites the notice: the existing body blocks are
    deleted first, then the new content is appended. That ordering is deliberate — the
    delete bumps ``revision_id``, so the append must re-read it rather than reuse the
    one it started with, or Feishu rejects the write on a stale lock.

    ``replace=False`` appends to whatever is already there, for adding a line to a
    standing notice without retyping it.

    Blank ``content`` with ``replace=True`` is refused rather than treated as "clear
    the announcement": wiping a group's notice is not something to do by accident. Use
    ``clear_chat_announcement_impl`` to say that explicitly.
    """
    cid = chat_id.strip()
    if not cid:
        return _core._error("chat_id is required (oc_...); resolve the group name first via feishu_api.")
    if not (content or "").strip():
        return _core._error(
            "content is empty — nothing to write. 要清空群公告请用 feishu_chat_announcement_clear (显式操作)。"
        )
    blocks = _core._content_to_blocks(content)
    if not blocks:
        return _core._error("content produced no blocks — nothing to write.")

    deleted = 0
    if replace:
        cleared = await clear_chat_announcement_impl(cid, user_key=user_key)
        if not cleared["ok"]:
            return cleared
        deleted = cleared["deleted"]

    # Re-read the revision: a delete above (or anyone else's edit) has moved it on.
    meta = await _announcement_meta(cid, user_key)
    if not meta["ok"]:
        return meta
    added = 0
    for start in range(0, len(blocks), _core._BLOCKS_BATCH):
        batch = blocks[start : start + _core._BLOCKS_BATCH]
        revision = meta["revision_id"]
        res = await _core._invoke(
            _build_announcement_children_request(cid, batch, revision, -1),
            user_key=user_key,
            prefer="tenant",
        )
        if not res["ok"]:
            return _with_hint({**res, "chat_id": cid, "added": added, "deleted": deleted}, _ANNOUNCEMENT_ERROR_HINTS)
        added += len(batch)
        # Each successful batch advances the document version; the next one must use it.
        data = res["data"] if isinstance(res["data"], dict) else {}
        next_revision = data.get("revision_id")
        meta = {**meta, "revision_id": next_revision if isinstance(next_revision, int) else revision + 1}
    return {
        "ok": True,
        "chat_id": cid,
        "added": added,
        "deleted": deleted,
        "replaced": replace,
        "revision_id": meta["revision_id"],
    }


async def clear_chat_announcement_impl(chat_id: str, user_key: str = "") -> dict[str, Any]:
    """Delete every body block of a group's 群公告, leaving it empty.

    Separate from ``set_chat_announcement_impl`` because emptying a group's notice is a
    destructive act with no undo, so it has to be asked for by name. Deleting nothing
    (an already-empty announcement) succeeds with ``deleted == 0`` rather than erroring.
    """
    cid = chat_id.strip()
    if not cid:
        return _core._error("chat_id is required (oc_...); resolve the group name first via feishu_api.")
    current = await read_chat_announcement_impl(cid, max_chars=1, user_key=user_key)
    if not current["ok"]:
        return current
    count = int(current.get("block_count") or 0)
    if count <= 0:
        return {"ok": True, "chat_id": cid, "deleted": 0, "revision_id": current.get("revision_id", 0)}
    res = await _core._invoke(
        _build_announcement_delete_request(cid, 0, count, int(current.get("revision_id") or 0)),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, _ANNOUNCEMENT_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    revision = data.get("revision_id")
    return {
        "ok": True,
        "chat_id": cid,
        "deleted": count,
        "revision_id": revision if isinstance(revision, int) else 0,
    }


# ── 群设置变更 / 解散群 / 转让群主 (chat update, delete, ownership) ───────────────
#
# All three ride the same two endpoints — PUT /open-apis/im/v1/chats/:chat_id for every
# setting (including ``owner_id``, which is how ownership transfer works) and DELETE on
# the same path to dismiss the group. They are separate tools because the *consequences*
# differ by an order of magnitude, and because Feishu's raw body is easy to get wrong in
# two specific ways that silently produce the opposite of what was asked:
#
# 1. **``add_member_permission`` and ``share_card_permission`` are coupled.** Feishu
#    rejects ``only_owner`` + ``allowed``. Sending one alone is accepted but leaves the
#    pair inconsistent, so the pair is completed here from whichever half was given.
# 2. **禁言 is not a field on this endpoint.** "全员禁言" lives on a *different* endpoint
#    (PUT .../moderation, ``moderation_setting``); an agent reaching for
#    ``moderation_permission`` on the update body gets a silently ignored field. Hence
#    ``update_chat_moderation_impl`` below, and the guard in ``update_chat_impl``.
#
# The human-facing vocabulary is deliberately Chinese-first: the agent is told "把群改名"
# / "开全员禁言", and mapping that onto Feishu's enum strings is exactly the knowledge
# that has to be *guaranteed* rather than remembered.
_CHAT_UPDATE_ERROR_HINTS = {
    232002: "该群限定「仅群主和管理员可编辑群信息」; 传群主/管理员的 user_key 以本人身份改。",
    232012: "指定的新群主还不是群成员; 先把他加进群 (POST /chats/:chat_id/members) 再转让。",
    232016: "普通成员只能改群头像/群名称/群描述/国际化名称; 其它设置要群主或管理员。",
    232020: "群名称不合法 (公开群至少 2 个字符)。",
    232021: "群头像 image_key 无效; 必须用 image_type='avatar' 上传 (feishu_chat_upload_avatar)。",
}


_MODERATION_ERROR_HINTS = {
    232060: "该群已被封禁, 无法修改发言权限。",
    232092: "群里正在开会, 此时改不了发言权限; 会议结束后重试。",
}


# ── 群菜单 (chat menu) — the buttons along the bottom of a group ─────────────────
#
# Two shapes of the same thing, and the API makes them awkward in a way worth absorbing:
# a first-level menu either *does* something (``action_type="REDIRECT_LINK"`` + a URL) or
# *contains* children (``action_type="NONE"``, no icon allowed). Feishu enforces that,
# but only after the request lands, so the combination is checked here.
#
# Create **appends** — it never replaces — and caps at 3 first-level menus with 5
# children each. Children cannot be added to a first-level menu that already exists, so
# a menu with sub-items has to be created in one call: the whole tree is built from a
# compact ``[{name, url?, children?}]`` list rather than Feishu's nested wrapper objects,
# which are three levels of single-key dicts an agent gets wrong more often than not.
_MENU_ERROR_HINTS = {
    232011: "机器人不在该群里, 先把机器人加入群。",
    232025: "应用未启用机器人能力, 到开发者后台开启后再试。",
    232055: "机器人没有管理群菜单的权限 (该群限定群主/管理员才能改)。",
    232056: "菜单图标 image_key 不是本机器人上传的; 用 feishu_message_upload_image 重新上传。",
    232090: "群类型不支持群菜单 (仅普通群 group)。",
}


# ── 群标签页 (chat tabs) — the pinned tabs across the top of a group ─────────────
#
# Feishu lists eleven ``tab_type`` values but only two can be *created*: ``doc`` and
# ``url``. The rest (pin / 会议纪要 / 任务 / 图片视频 …) are built-in tabs the API can only
# read. Trying to create one fails with an unhelpful parameter error, so unsupported
# types are refused here by name, with the two that work spelled out.
_TAB_ERROR_HINTS = {
    232046: "群标签页数量已达上限 (每个会话最多 20 个自定义标签页)。",
    232047: "标签页名称过长 (最多 60 字)。",
    232048: "tab_content 不合法; doc 类型要文档链接, url 类型要 http(s) 链接。",
    232050: "该会话类型不支持群标签页 (仅群组 group 和单聊 p2p)。",
    232051: "缺少该文档的权限; 先把文档共享给机器人 (或传本人 user_key)。",
    232055: "机器人没有管理群标签页的权限 (该群限定群主/管理员才能改)。",
}


async def upload_chat_avatar_impl(image_path: str, user_key: str = "") -> dict[str, Any]:
    """Upload a picture as a **group avatar** and return its ``image_key``.

    Separate from ``upload_image_impl`` for one reason that costs a debugging session to
    find: ``im/v1/images`` takes an ``image_type``, and a group avatar must be uploaded
    as ``"avatar"``. A ``message``-type key is accepted by the upload and then rejected
    by the chat-update call (232021), which reads as "bad avatar" rather than "wrong
    upload type".
    """
    data, name, bad = await _read_upload_bytes(image_path, _core._IMAGE_UPLOAD_MAX_BYTES, "avatar image")
    if bad is not None:
        return bad
    suffix = pathlib.Path(name).suffix.lower()
    if suffix and suffix not in _IMAGE_SUFFIXES:
        return _core._error(f"{name} is not an image Feishu accepts ({', '.join(sorted(_IMAGE_SUFFIXES))}).")
    res = await _core._invoke(
        lambda: _build_image_upload_request("avatar", name, data),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _UPLOAD_ERROR_HINTS)
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "image_key": rdata.get("image_key", ""), "file_name": name, "size": len(data)}


# ── 消息搜索 (message search) — find messages by keyword across chats ────────────
#
# Feishu's only keyword search over message *content*, and it is user-token-only: it
# searches what **that person** can see, so there is no bot-wide variant to fall back on
# (the tenant token is refused outright). Same auth path as docs search / global user
# search: the caller must have authorized once.
#
# The response is the sharp edge. Feishu returns **message_ids only** — no text, no
# sender, no chat. A search result the agent can't read is useless, so each hit is
# hydrated through ``im/v1/messages/:message_id`` and the text extracted with the same
# ``_message_plain_text`` the history tools use. Hydration is capped and failures are
# kept as bare ids rather than dropped, so a partial result stays honest about what it
# could not read (a message in a chat the *bot* is not in, typically).
_MESSAGE_SEARCH_HINTS = {
    99991663: "缺少用户授权; 消息搜索只能以本人身份进行 (tenant token 不被接受)。",
    99991400: "搜索参数不合法; 检查 start_time/end_time 是否为秒级时间戳。",
}
_MESSAGE_SEARCH_HYDRATE_MAX = 50
_MESSAGE_SEARCH_TYPES = ("file", "image", "media")
_MESSAGE_SEARCH_FROM_TYPES = ("bot", "user")
_MESSAGE_SEARCH_CHAT_TYPES = ("group_chat", "p2p_chat")


def _build_message_search_request(body: dict[str, Any], page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/search/v2/message"
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    # User token only: this searches what the authorizing person can see.
    req.token_types = {AccessTokenType.USER}
    req.body = body
    return req


def _message_search_body(
    query: str,
    chat_ids: list[str],
    from_ids: list[str],
    message_type: str,
    from_type: str,
    chat_type: str,
    start_time: str,
    end_time: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The search body; returns (body, error). Only named filters are included."""
    body: dict[str, Any] = {"query": query}
    if chat_ids:
        body["chat_ids"] = chat_ids
    if from_ids:
        body["from_ids"] = from_ids
    if message_type.strip():
        kind = message_type.strip().lower()
        if kind not in _MESSAGE_SEARCH_TYPES:
            return {}, _core._error(
                f"message_type 只能是 {', '.join(_MESSAGE_SEARCH_TYPES)} (按附件类型筛), 收到 {message_type!r}。"
                "搜纯文本消息不要传这个参数。"
            )
        body["message_type"] = kind
    if from_type.strip():
        sender = from_type.strip().lower()
        if sender not in _MESSAGE_SEARCH_FROM_TYPES:
            return {}, _core._error(f"from_type 只能是 {' 或 '.join(_MESSAGE_SEARCH_FROM_TYPES)}, 收到 {from_type!r}。")
        body["from_type"] = sender
    if chat_type.strip():
        where = chat_type.strip().lower()
        if where not in _MESSAGE_SEARCH_CHAT_TYPES:
            return {}, _core._error(f"chat_type 只能是 group_chat (群聊) 或 p2p_chat (单聊), 收到 {chat_type!r}。")
        body["chat_type"] = where
    # Feishu wants second-level timestamps as strings here (not the ms other endpoints
    # take), and a wrong unit silently matches nothing instead of erroring.
    for field, raw in (("start_time", start_time), ("end_time", end_time)):
        if not raw.strip():
            continue
        digits = raw.strip()
        if not digits.isdigit():
            return {}, _core._error(f"{field} 必须是秒级 Unix 时间戳 (如 '1609296809'), 收到 {raw!r}。")
        if len(digits) >= 13:
            return {}, _core._error(
                f"{field}={raw!r} 看起来是毫秒时间戳; 这个接口要**秒级** (10 位), 传毫秒会搜不到任何东西。"
            )
        body[field] = digits
    return body, None


async def _hydrate_message(message_id: str, user_key: str) -> dict[str, Any]:
    """One search hit turned into ``{message_id, chat_id, sender, text, create_time}``.

    Failure is reported per-hit (``readable: False``) instead of aborting the search: a
    hit in a chat the bot cannot read is a normal outcome, and the id alone still tells
    the caller the message exists.
    """
    res = await _core._invoke(_build_get_message_request(message_id), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return {"message_id": message_id, "readable": False, "reason": res.get("message", "")}
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items")
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else data
    raw_sender = item.get("sender")
    sender = raw_sender if isinstance(raw_sender, dict) else {}
    raw_body = item.get("body")
    body = raw_body if isinstance(raw_body, dict) else {}
    return {
        "message_id": message_id,
        "readable": True,
        "chat_id": item.get("chat_id", "") or "",
        "sender_id": sender.get("id", ""),
        "sender_type": sender.get("sender_type", ""),
        "msg_type": body.get("message_type") or item.get("msg_type", "") or "",
        "create_time": item.get("create_time", "") or "",
        "text": _message_plain_text(item),
    }


async def search_messages_impl(
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
) -> dict[str, Any]:
    """Search message content by keyword across the caller's chats (全局消息搜索).

    Searches as **the caller**, so it finds what that person can see and needs their
    authorization — Feishu accepts no tenant token here, which is why there is no
    bot-wide variant. ``user_key`` is therefore required, not optional.

    Feishu returns message ids only; each hit is read back so the result carries the
    actual ``text``, ``chat_id`` and sender. Hits the bot cannot read come back with
    ``readable: false`` and their id, rather than being dropped — usually a chat the bot
    isn't in, which the caller may want to know about.

    Filters narrow rather than widen: ``chat_ids`` to particular groups, ``from_ids`` to
    particular senders, ``start_time``/``end_time`` as **second**-level timestamps.
    """
    keyword = (query or "").strip()
    if not keyword:
        return _core._error("query is required — 要搜的关键词。")
    key = user_key.strip()
    if not key:
        return _core._error(
            "user_key is required — 消息搜索只能以本人身份进行 (飞书不接受机器人 token), "
            "传 <feishu_context> 里的 sender_open_id。"
        )
    body, bad = _message_search_body(
        keyword,
        [c.strip() for c in (chat_ids or []) if c and c.strip()],
        [f.strip() for f in (from_ids or []) if f and f.strip()],
        message_type,
        from_type,
        chat_type,
        start_time,
        end_time,
    )
    if bad is not None:
        return bad
    cap = max(1, min(int(limit or 20), _MESSAGE_SEARCH_HYDRATE_MAX))

    ids: list[str] = []
    page_token = ""
    has_more = False
    while True:
        res = await _core._invoke(
            _build_message_search_request(body, min(cap - len(ids), _MESSAGE_SEARCH_HYDRATE_MAX), page_token),
            user_key=key,
            prefer="user",
            identity="user",
            capabilities=[],
        )
        if not res["ok"]:
            return _with_hint(res, _MESSAGE_SEARCH_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            # Feishu documents items as a list of message_id strings; tolerate an object
            # form too rather than returning nothing if that ever changes.
            if isinstance(raw, str) and raw:
                ids.append(raw)
            elif isinstance(raw, dict) and raw.get("message_id"):
                ids.append(str(raw["message_id"]))
        page_token = str(data.get("page_token") or "")
        has_more = bool(data.get("has_more"))
        if not has_more or not page_token or len(ids) >= cap:
            break

    ids = ids[:cap]
    messages = [await _hydrate_message(mid, key) for mid in ids]
    return {
        "ok": True,
        "query": keyword,
        "filters": {k: v for k, v in body.items() if k != "query"},
        "messages": messages,
        "count": len(messages),
        "unreadable": len([m for m in messages if not m.get("readable")]),
        "has_more": has_more and len(ids) >= cap,
    }


# ── Approval event subscription — enable push (no polling) ────────────────────
#
# Subscribing an approval definition makes Feishu push an ``approval_instance``
# event over the app's event channel (the same WebSocket the bot already runs)
# every time an instance of that definition changes status. The channel layer
# turns those events into a proactive DM to the applicant — so status changes are
# pushed, never polled. Subscribe is idempotent per app: one call per approval
# definition is enough (repeat calls are a no-op on Feishu's side).


# ── Start a group topic with @-mentions ──────────────────────────────────────
#
# Text messages' <at> tags do NOT render as real mentions for bots (Feishu shows
# the raw tag). Real mentions require the "post" rich-text message type, whose
# `at` element ({"tag":"at","user_id":...}) does render. So when mentions are
# requested we send a post; with no mentions we keep a plain text message.


def _build_post_at_content(text: str, at_open_ids: list[str], at_all: bool) -> str:
    """Build a post rich-text content JSON string: leading @ elements, then the text run."""
    line: list[dict[str, Any]] = []
    if at_all:
        line.append({"tag": "at", "user_id": "all"})
    line.extend({"tag": "at", "user_id": oid} for oid in at_open_ids if oid)
    # separate mentions from the message with a space, then the text
    line.append({"tag": "text", "text": f" {text}" if line else text})
    return json.dumps({"zh_cn": {"title": "", "content": [line]}}, ensure_ascii=False)


async def start_topic_impl(
    chat_id: str,
    text: str,
    at_open_ids: list[str] | None = None,
    at_all: bool = False,
) -> dict[str, Any]:
    """Post a topic root message to a group, @-mentioning the given open_ids (and/or everyone).

    Uses a post rich-text message when mentions are requested (so @ renders), a
    plain text message otherwise. Returns message_id + thread_id (the topic root).
    """
    ids = at_open_ids or []
    if ids or at_all:
        content = _build_post_at_content(text, ids, at_all)
        req = _build_send_message_request(chat_id, "chat_id", "post", content)
    else:
        content = json.dumps({"text": text}, ensure_ascii=False)
        req = _build_send_message_request(chat_id, "chat_id", "text", content)
    res = await _core._invoke(req)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", "") or chat_id,
    }


# ── Read status: who has read a message (已读 / 未读) ──────────────────────────
#
# GET /open-apis/im/v1/messages/:message_id/read_users answers only half the
# question: it returns the users who HAVE read the message and there is no
# "unread users" endpoint at all. So 未读 is computed here — pull the chat's
# roster and subtract the readers — because the alternative is every caller
# reporting "3 人已读" and staying silent about the 12 who haven't.
#
# That diff needs the message's chat_id, which the caller rarely has at hand, so
# it is resolved from the message itself (GET on the message) instead of being
# demanded as an argument. The sender is excluded from 未读: the bot obviously
# read its own message and Feishu never lists it as a reader.
#
# Two limits are invisible in the raw error text and are exactly what this API
# trips over: only the bot's OWN messages can be queried (230012), and only
# within 7 days of sending (230033).

_READ_STATUS_ERROR_HINTS = {
    230001: "请求参数不合法 (message_id 必须是 om_... 开头的消息 id)。",
    230002: "机器人不在该会话里, 先把机器人加入群再查询已读情况。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230012: "只能查询机器人自己发出的消息的已读情况; 别人发的消息查不了 (飞书不开放)。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230027: "缺少查询已读所需权限 (im:message / im:message:readonly / im:message:basic); 外部群不支持。",
    230033: "超出 7 天查询窗口: 只能查询发送后 7 天以内的消息。",
    230110: "该消息已被撤回或删除, 无法查询已读情况。",
}


def _build_read_users_request(message_id: str, user_id_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id/read_users"
    req.paths["message_id"] = message_id
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", max(1, min(page_size, 100)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_get_message_request(message_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _message_chat_and_sender(message_id: str, user_key: str = "") -> tuple[str, str]:
    """The ``(chat_id, sender_id)`` of a message, or ``("", "")`` if it can't be read.

    Used to locate the roster for an unread diff without making the caller pass a
    chat_id they'd have to dig up. Failure is not fatal to the caller: the read
    list is still worth returning without the unread half.
    """
    res = await _core._invoke(_build_get_message_request(message_id), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return "", ""
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items")
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else data
    sender = item.get("sender")
    sender_id = sender.get("id", "") if isinstance(sender, dict) else ""
    return item.get("chat_id", "") or "", sender_id or ""


async def read_status_impl(
    message_id: str,
    include_unread: bool = True,
    page_size: int = 100,
    user_key: str = "",
) -> dict[str, Any]:
    """Who has read a message the bot sent — and, by diff, who hasn't.

    Pages through the readers in full (the caller wants a roll-call, not page 1)
    and, when ``include_unread``, subtracts them from the chat's roster to get the
    people who still haven't. The sender is left out of both lists.

    Only the bot's own messages, sent within 7 days, can be queried at all — both
    limits come back as a ``hint`` rather than a bare ``2300xx``.
    """
    mid, bad = _require_message_id(message_id, "check the read status of")
    if bad is not None:
        return bad

    readers: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _core._invoke(
            _build_read_users_request(mid, "open_id", page_size, page_token), user_key=user_key, prefer="tenant"
        )
        if not res["ok"]:
            return _with_hint(res, _READ_STATUS_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        raw_items = data.get("items")
        items: list[Any] = raw_items if isinstance(raw_items, list) else []
        for it in items:
            if isinstance(it, dict):
                readers.append({"open_id": it.get("user_id", ""), "read_time": it.get("timestamp", "")})
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    result: dict[str, Any] = {
        "ok": True,
        "message_id": mid,
        "read_users": readers,
        "read_count": len(readers),
    }
    if not include_unread:
        return result
    return {**result, **await _unread_from_roster(mid, readers, user_key)}


async def list_chat_members_impl(
    chat_id: str,
    member_id_type: str = "open_id",
) -> dict[str, Any]:
    """List every member of a group. Pages through the full roster automatically.

    Unlike ``find_member_id_impl`` (which matches by name), this returns the whole
    roster in one call. Returns members [{name, id, member_id_type}].
    """
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _core._invoke(_build_chat_members_request(chat_id, member_id_type, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "name": it.get("name", ""),
                    "id": it.get("member_id", ""),
                    "member_id_type": it.get("member_id_type", member_id_type),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    return {
        "ok": True,
        "chat_id": chat_id,
        "members": members,
        "count": len(members),
    }


async def _unread_from_roster(message_id: str, readers: list[dict[str, str]], user_key: str) -> dict[str, Any]:
    """The unread half of a read-status answer: chat roster minus readers minus sender.

    Kept separate because it is best-effort — a p2p chat, a roster the bot may not
    list, or an unreadable message each cost the unread list but not the read one,
    so every failure returns a ``note`` instead of an error.
    """
    chat_id, sender_id = await _message_chat_and_sender(message_id, user_key)
    if not chat_id:
        return {"note": "未读名单需要消息所在会话的成员列表, 但这条消息读不到 (可能已撤回或机器人不可见)。"}
    roster = await list_chat_members_impl(chat_id)
    if not roster.get("ok"):
        return {
            "chat_id": chat_id,
            "note": f"已读名单已取到, 但群成员列表拉取失败, 无法算未读: {roster.get('message', '')}".strip(),
        }
    read_ids = {r["open_id"] for r in readers if r.get("open_id")}
    unread = [
        {"open_id": m["id"], "name": m.get("name", "")}
        for m in roster.get("members", [])
        if m.get("id") and m["id"] not in read_ids and m["id"] != sender_id
    ]
    return {
        "chat_id": chat_id,
        "unread_users": unread,
        "unread_count": len(unread),
        "member_count": roster.get("count", 0),
    }


# ── Pin / unpin a message (置顶) ───────────────────────────────────────────────
#
# POST /open-apis/im/v1/pins pins, DELETE /open-apis/im/v1/pins/:message_id
# unpins, GET /open-apis/im/v1/pins lists a group's pins (newest first).
#
# Two behaviours are worth not fighting: pinning an already-pinned message
# returns the existing pin rather than an error, and unpinning a message that was
# never pinned succeeds. Both are reported honestly as ok rather than dressed up.
#
# 230046 is the one that actually bites: many groups restrict 置顶 to the owner or
# admins, and the bot is usually neither. That needs a *person's* identity
# (``user_key`` + authorization), which the hint says outright instead of leaving
# a bare "no permission".

_PIN_ERROR_HINTS = {
    230001: "请求参数不合法 (message_id 必须是 om_... 开头的消息 id)。",
    230002: "机器人不在该群里, 先把机器人加入群再置顶。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230011: "该消息已被撤回, 无法置顶。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230027: "缺少 Pin 所需权限 (im:message / im:message.pins:write_only / im:message:send_as_bot); "
    "外部群还需开启对外共享。",
    230045: "会话不存在 (群可能已解散)。",
    230046: "该群限制只有群主/管理员能置顶: 用管理员本人身份操作 (传其 user_key 并完成授权), 或让群主放开权限。",
    230047: "同一条消息的置顶/取消置顶操作过于频繁 (上限 5 QPS), 稍后再试。",
    230048: "获取群 Pin 列表过于频繁, 稍后再试。",
    230050: "该消息对当前操作身份不可见, 无法置顶。",
    230054: "该消息类型不支持置顶。",
    230111: "该消息即将自动销毁, 不支持此操作。",
    232009: "群组已解散, 无法操作。",
}


# ── Forward a message to another chat (转发 / 合并转发) ────────────────────────
#
# POST /open-apis/im/v1/messages/:message_id/forward moves one message to another
# target; POST /open-apis/im/v1/messages/merge_forward bundles 1-100 messages from
# the SAME conversation into a single 合并转发 card.
#
# Forwarding preserves the original's attribution and content, which is the point:
# re-sending the text with feishu_message_send loses who said it and silently
# drops any attachment. The trade is that the content cannot be altered — to add
# a remark, forward and then send a comment separately.
#
# Both endpoints accept a thread_id (``omt_...``) as the target, which the shared
# id inference doesn't know (feishu_message_send cannot send to a thread), so
# forwarding resolves the type itself.

_FORWARD_ERROR_HINTS = {
    230001: "请求参数不合法 (message_id 必须是 om_..., receive_id 与其类型要匹配)。",
    230002: "机器人不在目标群里, 先把机器人加入目标群再转发。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230018: "目标群当前设置不允许该操作 (如全员禁言)。",
    230019: "目标话题 (thread) 不存在。",
    230020: "转发过于频繁, 触发限流 (单个目标 5 QPS), 稍后再试。",
    230027: "缺少转发所需权限 (im:message / im:message:send_as_bot); 外部群还需开启对外共享。",
    230029: "目标用户已离职。",
    230034: "receive_id 不合法, 或与 receive_id_type 不匹配。",
    230035: "没有向目标会话发消息的权限 (可能被禁言, 或机器人被屏蔽)。",
    230038: "跨租户单聊不允许该操作。",
    230049: "原消息还在发送中, 稍等再转发。",
    230050: "原消息对当前身份不可见, 无法转发。",
    230053: "该用户已停止接收机器人消息。",
    230061: "该消息类型不支持转发 (红包/投票/语音/日程转让/系统消息等不可转发)。",
    230062: "没有权限转发到第三方加密群。",
    230063: "目标群 chat_id 不合法。",
    230064: "要转发的消息不合法 (合并转发的子消息不能再次转发)。",
    230065: "要转发的消息已被撤回。",
    230066: "密聊消息不支持转发。",
    230067: "合并转发的消息来源不合规 (不能跨多个话题, 也不能混合普通消息和话题回复)。",
    230069: "合并转发的消息必须来自同一个会话, 当前这批跨了不同群。",
    230070: "限制模式下不允许转发。",
    230074: "目标话题对当前身份不可见。",
    230110: "原消息已被删除, 无法转发。",
    232009: "群组已解散, 无法转发。",
}
