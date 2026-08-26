"""Feishu interactive-card callback parsing, consumption, and dispatch."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import InputChunk, TextChunk

from ._card_store import (
    CardSnapshot,
    card_claim_guard,
    peek_card_multi_use,
    pop_card_snapshot,
    rewrite_card_snapshot,
)

_INTERACTIVE_CARD_TAGS = {"action", "button", "form"}
_REMOVED_CARD_ELEMENT = object()

type ResolveCore = Callable[[str | None], Awaitable[ChannelCore]]
type MarkSeen = Callable[[str], bool]
type RunCardBatch = Callable[[list[str]], Awaitable[None]]


class CardActionBatcher:
    """Coalesce card clicks that land while the agent is still answering.

    A tick repaints the card immediately, but the agent turn behind it takes seconds,
    and ``SessionAgent`` holds one lock per session. Without coalescing, an impatient
    user's N ticks queue N turns and earn N replies — and the queue is what makes them
    keep clicking. Clicks arriving mid-flight are merged into one follow-up turn.

    Keyed per (card, clicker), never per card alone: two people ticking the same group
    card must keep their own sessions and their own replies.
    """

    def __init__(self) -> None:
        self._pending: dict[str, list[str]] = {}
        self._running: set[str] = set()

    async def submit(self, key: str, context: str, run: RunCardBatch) -> None:
        """Queue one click, running it now unless a turn for ``key`` already owns it."""
        self._pending.setdefault(key, []).append(context)
        if key in self._running:
            logger.info(f"card action merged into in-flight turn key={key}")
            return
        self._running.add(key)
        try:
            while True:
                batch = self._pending.pop(key, [])
                if not batch:
                    return
                if len(batch) > 1:
                    logger.info(f"running {len(batch)} coalesced card actions key={key}")
                await run(batch)
        finally:
            self._running.discard(key)
            # Only a raising run() leaves anything behind; dropping it is safer than replaying
            # it for whoever clicks next.
            self._pending.pop(key, None)


def _batched_card_context(contexts: list[str]) -> str:
    """Join coalesced click payloads into one agent turn."""
    if len(contexts) == 1:
        return contexts[0]
    body = "\n".join(contexts)
    return f'<feishu_card_action_batch count="{len(contexts)}">\n{body}\n</feishu_card_action_batch>'


class StreamReply(Protocol):
    async def __call__(
        self,
        channel: Any,
        core: ChannelCore,
        chat_id: str,
        chunks: list[InputChunk],
        *,
        reply_to: str | None,
        suppress_silent_reply: bool = False,
    ) -> None: ...


def _normalize_card_action_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _contains_card_action_value(value: Any, action_value: Any) -> bool:
    if isinstance(value, dict):
        if "value" in value and _normalize_card_action_value(value["value"]) == action_value:
            return True
        return any(_contains_card_action_value(child, action_value) for child in value.values())
    if isinstance(value, list):
        return any(_contains_card_action_value(child, action_value) for child in value)
    return False


def _find_card_action_label(value: Any, action_value: Any) -> str | None:
    normalized_action_value = _normalize_card_action_value(action_value)
    if isinstance(value, dict):
        direct_match = "value" in value and _normalize_card_action_value(value["value"]) == normalized_action_value
        behavior_match = value.get("tag") == "button" and _contains_card_action_value(
            value.get("behaviors"), normalized_action_value
        )
        if direct_match or behavior_match:
            text = value.get("text")
            if isinstance(text, str):
                label = text or None
            elif isinstance(text, dict):
                content = text.get("content")
                label = content if isinstance(content, str) and content else None
            else:
                label = None
            if label:
                return label
        for child in value.values():
            label = _find_card_action_label(child, action_value)
            if label:
                return label
    elif isinstance(value, list):
        for child in value:
            label = _find_card_action_label(child, action_value)
            if label:
                return label
    return None


def _remove_card_interactions(
    value: Any,
    action_value: Any,
    selected_element: dict[str, Any],
    selected_replaced: bool = False,
    *,
    keep_others: bool = False,
) -> tuple[Any, bool]:
    """Replace the clicked interactive element, dropping the rest.

    ``keep_others`` is the multi-use (TODO list) mode: every row other than the clicked
    one keeps its button, so the remaining items stay tickable.
    """
    if isinstance(value, dict):
        if value.get("tag") in _INTERACTIVE_CARD_TAGS:
            if not selected_replaced and _find_card_action_label(value, action_value):
                return selected_element, True
            return (value, selected_replaced) if keep_others else (_REMOVED_CARD_ELEMENT, selected_replaced)

        result: dict[str, Any] = {}
        for key, child in value.items():
            cleaned, selected_replaced = _remove_card_interactions(
                child,
                action_value,
                selected_element,
                selected_replaced,
                keep_others=keep_others,
            )
            if cleaned is not _REMOVED_CARD_ELEMENT:
                result[key] = cleaned
        return result, selected_replaced

    if isinstance(value, list):
        result: list[Any] = []
        for child in value:
            cleaned, selected_replaced = _remove_card_interactions(
                child,
                action_value,
                selected_element,
                selected_replaced,
                keep_others=keep_others,
            )
            if cleaned is not _REMOVED_CARD_ELEMENT:
                result.append(cleaned)
        return result, selected_replaced

    return value, selected_replaced


def _consumed_card_content(card: Any, action_value: Any, *, multi_use: bool = False) -> dict[str, Any] | None:
    if action_value is None or not isinstance(card, dict):
        return None
    selected_label = _find_card_action_label(card, action_value)
    if not selected_label:
        return None
    if multi_use:
        # The placeholder IS a button — the two buttons swap in place with no
        # intermediate text flash: tick → 「撤销」button, untick → 「○ 标记完成」
        # button (same label the tool's rebuild will render a moment later, so the
        # swap reads seamlessly). The placeholder reuses the clicked button's own
        # value: if the user taps it again before the rebuild lands, the callback
        # carries the already-consumed action id and Channel's mark_seen dedup
        # ignores it — no side effects.
        payload = _normalize_card_action_value(action_value)
        payload = payload if isinstance(payload, dict) else {}
        action_id = str(payload.get("action") or "")
        # 按钮互转占位只属于 todo 卡(tick/untick 的撤销往返);
        # 其他 multi_use 卡(如评价卡的 review_score)不做占位,
        # 卡片外观由回调处理方(工具/海豚)原位更新决定。
        if payload and action_id and action_id.startswith("todo_"):
            next_label = "○ 标记完成" if action_id.startswith("todo_untick") else "撤销"
            selected_element = {
                "tag": "button",
                "text": {"tag": "plain_text", "content": next_label},
                "type": "default",
                "value": payload,
            }
        elif payload and action_id:
            # 非 todo 动作(如评价卡的 review_score):不做占位替换,
            # 卡片外观由回调处理方原位更新决定。
            return None
        else:
            # Legacy value shape (no action id) — keep the old struck-through label.
            selected_element = {
                "tag": "markdown",
                "content": f"● ~~{selected_label}~~",
            }
    elif card.get("schema") == "2.0":
        selected_element = {
            "tag": "markdown",
            "content": f"已选择: {selected_label}",
        }
    else:
        selected_element = {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"已选择: {selected_label}",
                }
            ],
        }
    consumed, selected_replaced = _remove_card_interactions(
        card,
        action_value,
        selected_element,
        keep_others=multi_use,
    )
    return consumed if selected_replaced and isinstance(consumed, dict) else None


def _card_has_action_value(value: Any) -> bool:
    """Report whether any interactive element still carries a callback value.

    ``fetch_message`` returns the *rendered* card, where a button collapses to
    ``{"tag": "button", "text": "...", "type": "primary"}`` with no ``value`` and no
    ``behaviors``. Nothing downstream can then tell which button was clicked, so the
    fetch fallback is structurally incapable of preserving such a card — as opposed to
    merely having missed on this particular action. Distinguishing the two keeps the
    warning honest about which situation the operator is looking at.
    """
    if isinstance(value, dict):
        if "value" in value or "behaviors" in value:
            return True
        return any(_card_has_action_value(child) for child in value.values())
    if isinstance(value, list):
        return any(_card_has_action_value(child) for child in value)
    return False


def _fallback_card_content(original_card: Any) -> dict[str, Any]:
    """Build the generic "已提交" card in the same schema as the card being replaced.

    Feishu rejects a v1 body sent for a schema-2.0 original with
    ``ErrCode: 200830; ErrMsg: schemaV2 card can not change schemaV1``, so a hardcoded
    v1 fallback leaves 2.0 cards visibly untouched — the button stays clickable and the
    operator gets no feedback at all. Mirroring the original's schema keeps the fallback
    usable for both generations; an unknown original stays on v1, which is what the
    legacy cards in this codebase use.
    """
    notice = {"tag": "markdown", "content": "你的操作已提交, 请查看本会话中的处理结果。"}
    title = {"tag": "plain_text", "content": "已提交"}
    if isinstance(original_card, dict) and original_card.get("schema") == "2.0":
        return {
            "schema": "2.0",
            "header": {"title": title, "template": "green"},
            "body": {"elements": [notice]},
        }
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green", "title": title},
        "elements": [notice],
    }


def _card_action_context(
    event: Any,
    *,
    snapshot: CardSnapshot | None = None,
    card: dict[str, Any] | None = None,
    snapshot_status: str = "not_found",
) -> str:
    """Serialize card/source/business data and deterministic dispatch as agent input."""
    operator = getattr(event, "operator", None)
    action = getattr(event, "action", None)
    raw = getattr(event, "raw", None)
    raw_event = raw.get("event") if isinstance(raw, dict) else None
    raw_action = raw_event.get("action") if isinstance(raw_event, dict) else None
    if not isinstance(raw_action, dict):
        raw_action = {}

    tag = getattr(action, "tag", None) or raw_action.get("tag") or ""
    value = getattr(action, "value", None)
    if value is None:
        value = raw_action.get("value")

    normalized_value = _normalize_card_action_value(value)
    action_id = None
    if isinstance(normalized_value, dict):
        for key in ("action", "action_id"):
            raw_action_id = normalized_value.get(key)
            if isinstance(raw_action_id, str) and raw_action_id and raw_action_id.strip() == raw_action_id:
                action_id = raw_action_id
                break

    action_handlers = snapshot.action_handlers if snapshot is not None else None
    if snapshot is None:
        handler = None
        strategy = "snapshot_invalid" if snapshot_status == "invalid" else "snapshot_unavailable"
    elif action_handlers:
        handler = action_handlers.get(action_id or "")
        strategy = "action_handlers"
    else:
        handler = action_id
        strategy = "action_id"

    payload = {
        "schema_version": 2,
        "chat_id": getattr(event, "chat_id", "") or "",
        "message_id": getattr(event, "message_id", "") or "",
        "operator_open_id": getattr(operator, "open_id", "") or "",
        "source": snapshot.source if snapshot is not None else {},
        "card": snapshot.card if snapshot is not None else card or {},
        "business_context": snapshot.business_context if snapshot is not None else {},
        "dispatch": {
            "action_id": action_id,
            "handler": handler,
            "matched": handler is not None,
            "strategy": strategy,
        },
        "action": {
            "tag": tag,
            "value": value,
            "name": getattr(action, "name", None) or raw_action.get("name"),
            "option": getattr(action, "option", None) or raw_action.get("option"),
            "timezone": getattr(action, "timezone", None) or raw_action.get("timezone"),
            "form_value": getattr(action, "form_value", None) or raw_action.get("form_value"),
            "input_value": getattr(action, "input_value", None) or raw_action.get("input_value"),
            "options": getattr(action, "options", None) or raw_action.get("options"),
            "checked": getattr(action, "checked", None)
            if getattr(action, "checked", None) is not None
            else raw_action.get("checked"),
        },
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"<feishu_card_action>\n{body}\n</feishu_card_action>"


def _action_id_of(action_value: Any) -> str | None:
    """The canonical action id inside a callback value, or ``None``."""
    normalized = _normalize_card_action_value(action_value)
    if not isinstance(normalized, dict):
        return None
    for key in ("action", "action_id"):
        raw = normalized.get(key)
        if isinstance(raw, str) and raw and raw.strip() == raw:
            return raw
    return None


async def handle_card_action(
    channel: Any,
    resolve_core: ResolveCore,
    allowed_ids: list[str] | None,
    mark_seen: MarkSeen,
    stream_reply: StreamReply,
    event: Any,
    appdata: str = "",
    batcher: CardActionBatcher | None = None,
) -> None:
    """Route a Feishu card action into the operator's agent session."""
    chat_id = ""
    try:
        operator = getattr(event, "operator", None)
        operator_open_id = getattr(operator, "open_id", None)
        chat_id = getattr(event, "chat_id", "") or ""
        message_id = getattr(event, "message_id", "") or ""

        if not operator_open_id:
            logger.warning("card action missing operator.open_id, skipping")
            return
        if not chat_id:
            logger.warning("card action missing chat_id, skipping")
            return
        if not message_id:
            logger.warning("card action missing message_id, cannot enforce single-use card")
            return
        if allowed_ids is not None and operator_open_id not in allowed_ids:
            logger.debug(f"card action operator {operator_open_id} blocked by whitelist")
            return

        action = getattr(event, "action", None)
        action_value = getattr(action, "value", None)
        if action_value is None:
            raw = getattr(event, "raw", None)
            raw_event = raw.get("event") if isinstance(raw, dict) else None
            raw_action = raw_event.get("action") if isinstance(raw_event, dict) else None
            action_value = raw_action.get("value") if isinstance(raw_action, dict) else None

        action_id = _action_id_of(action_value)
        try:
            multi_use = await peek_card_multi_use(message_id, appdata)
        except Exception as e:
            logger.warning(f"failed to read card mode {message_id}, treating as single-use — {e!r}")
            multi_use = False
        # A multi-use card drops the dedup grain from message_id to (message_id, action_id):
        # each todo on one card stands alone. Without an action id there is nothing to tell
        # them apart, so such a callback falls back to whole-card dedup.
        seen_key = f"{message_id}:{action_id}" if multi_use and action_id else message_id
        if not mark_seen(seen_key):
            logger.info(f"card action ignored for already-consumed key={seen_key}")
            return

        snapshot = None
        snapshot_status = "error"
        original_card = None
        replacement = None
        try:
            # Read-modify-write has to sit in one critical section: locking only the rewrite
            # would still let two ticks each read the pristine card, and the second would
            # overwrite the first row's completion back to open.
            async with card_claim_guard(message_id):
                claim = await pop_card_snapshot(
                    message_id,
                    appdata,
                    action_id=action_id if multi_use else None,
                )
                if claim.status == "already_consumed":
                    logger.info(
                        f"card action rejected by tombstone message={message_id} "
                        f"action={claim.rejected_action_id or action_id} operator={operator_open_id} "
                        f"multi_use={multi_use} rejected_so_far={claim.rejected_count}"
                    )
                    return
                snapshot_status = claim.status
                snapshot = claim.snapshot
                if snapshot is not None:
                    original_card = snapshot.card
                    replacement = _consumed_card_content(snapshot.card, action_value, multi_use=snapshot.multi_use)
                    if replacement is None:
                        logger.warning(f"failed to consume card snapshot {message_id}, trying Feishu payload")
                    elif snapshot.multi_use and not await rewrite_card_snapshot(message_id, replacement, appdata):
                        # The write-back is mandatory: the next click must render from the
                        # already-ticked card, or it undoes the previous row's completion.
                        logger.warning(f"failed to persist ticked card {message_id}, next tick may render stale rows")
        except Exception as e:
            logger.warning(f"failed to load card snapshot {message_id}, trying Feishu payload — {e!r}")

        if replacement is None:
            try:
                payload = await channel.fetch_message(message_id)
                fetched_card = None
                if isinstance(payload, dict):
                    data = payload.get("data")
                    items = data.get("items") if isinstance(data, dict) else None
                    if isinstance(items, list):
                        for item in items:
                            if not isinstance(item, dict) or item.get("msg_type") != "interactive":
                                continue
                            body = item.get("body")
                            content = body.get("content") if isinstance(body, dict) else None
                            if isinstance(content, dict):
                                fetched_card = content
                                break
                            if not isinstance(content, str):
                                continue
                            try:
                                parsed_card = json.loads(content)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(parsed_card, dict):
                                fetched_card = parsed_card
                                break
                if original_card is None:
                    original_card = fetched_card
                replacement = _consumed_card_content(fetched_card, action_value, multi_use=multi_use)
                if replacement is None:
                    if fetched_card is not None and not _card_has_action_value(fetched_card):
                        logger.warning(
                            f"cannot preserve consumed card {message_id}: the fetched card is the "
                            "rendered form and carries no action value, so the clicked element "
                            "cannot be identified — using fallback. Recover the snapshot instead "
                            "(check that the channel and gateway resolve the same appdata root)."
                        )
                    else:
                        logger.warning(f"failed to preserve consumed card {message_id}, using fallback")
            except Exception as e:
                logger.warning(f"failed to fetch consumed card {message_id}, using fallback — {e!r}")

        if replacement is None:
            replacement = _fallback_card_content(original_card)
        try:
            result = await channel.update_card(message_id, replacement)
            if not getattr(result, "success", False):
                logger.warning(f"failed to mark card {message_id} consumed — {getattr(result, 'error', None)!r}")
        except Exception as e:
            logger.warning(f"failed to mark card {message_id} consumed — {e!r}")

        core = await resolve_core(operator_open_id)
        logger.debug(
            f"card action operator={operator_open_id} chat={chat_id} "
            f"message={message_id or None} socket={core.session_socket}"
        )
        context = _card_action_context(
            event,
            snapshot=snapshot,
            card=original_card,
            snapshot_status=snapshot_status,
        )

        async def _run(batch: list[str]) -> None:
            await stream_reply(
                channel,
                core,
                chat_id,
                [TextChunk(_batched_card_context(batch))],
                reply_to=message_id or None,
                suppress_silent_reply=True,
            )

        if batcher is None or not multi_use:
            # A single-use card can only be clicked once, so there is no second click to merge.
            await _run([context])
        else:
            await batcher.submit(f"{message_id}:{operator_open_id}", context, _run)
        logger.debug("card action stream completed")
    except Exception as e:
        logger.error(f"Card action handling error — {e!r}")
        if not chat_id:
            return
        try:
            await channel.send(chat_id, {"text": f"Error: {e}"})
        except Exception as notify_error:
            logger.error(f"Card action error notification failed — {notify_error!r}")
