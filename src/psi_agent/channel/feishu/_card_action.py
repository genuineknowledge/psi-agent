"""Feishu interactive-card callback parsing, consumption, and dispatch."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import InputChunk, TextChunk

from ._card_store import CardSnapshot, pop_card_snapshot

_INTERACTIVE_CARD_TAGS = {"action", "form"}
_REMOVED_CARD_ELEMENT = object()

type ResolveCore = Callable[[str | None], Awaitable[ChannelCore]]
type MarkSeen = Callable[[str], bool]


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


def _find_card_action_label(value: Any, action_value: Any) -> str | None:
    normalized_action_value = _normalize_card_action_value(action_value)
    if isinstance(value, dict):
        if "value" in value and _normalize_card_action_value(value["value"]) == normalized_action_value:
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
    selected_label: str,
    selected_replaced: bool = False,
) -> tuple[Any, bool]:
    if isinstance(value, dict):
        if value.get("tag") in _INTERACTIVE_CARD_TAGS:
            if not selected_replaced and _find_card_action_label(value, action_value):
                return (
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": f"已选择: {selected_label}",
                            }
                        ],
                    },
                    True,
                )
            return _REMOVED_CARD_ELEMENT, selected_replaced

        result: dict[str, Any] = {}
        for key, child in value.items():
            cleaned, selected_replaced = _remove_card_interactions(
                child,
                action_value,
                selected_label,
                selected_replaced,
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
                selected_label,
                selected_replaced,
            )
            if cleaned is not _REMOVED_CARD_ELEMENT:
                result.append(cleaned)
        return result, selected_replaced

    return value, selected_replaced


def _consumed_card_content(card: Any, action_value: Any) -> dict[str, Any] | None:
    if action_value is None or not isinstance(card, dict):
        return None
    selected_label = _find_card_action_label(card, action_value)
    if not selected_label:
        return None
    consumed, selected_replaced = _remove_card_interactions(card, action_value, selected_label)
    return consumed if selected_replaced and isinstance(consumed, dict) else None


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


async def handle_card_action(
    channel: Any,
    resolve_core: ResolveCore,
    allowed_ids: list[str] | None,
    mark_seen: MarkSeen,
    stream_reply: StreamReply,
    event: Any,
    appdata: str = "",
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
        if not mark_seen(message_id):
            logger.info(f"card action ignored for already-consumed message={message_id}")
            return

        action = getattr(event, "action", None)
        action_value = getattr(action, "value", None)
        if action_value is None:
            raw = getattr(event, "raw", None)
            raw_event = raw.get("event") if isinstance(raw, dict) else None
            raw_action = raw_event.get("action") if isinstance(raw_event, dict) else None
            action_value = raw_action.get("value") if isinstance(raw_action, dict) else None

        snapshot = None
        snapshot_status = "error"
        original_card = None
        replacement = None
        try:
            claim = await pop_card_snapshot(message_id, appdata)
            if claim.status == "already_consumed":
                logger.info(f"card action ignored for durably-consumed message={message_id}")
                return
            snapshot_status = claim.status
            snapshot = claim.snapshot
            if snapshot is not None:
                original_card = snapshot.card
                replacement = _consumed_card_content(snapshot.card, action_value)
                if replacement is None:
                    logger.warning(f"failed to consume card snapshot {message_id}, trying Feishu payload")
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
                replacement = _consumed_card_content(fetched_card, action_value)
                if replacement is None:
                    logger.warning(f"failed to preserve consumed card {message_id}, using fallback")
            except Exception as e:
                logger.warning(f"failed to fetch consumed card {message_id}, using fallback — {e!r}")

        if replacement is None:
            replacement = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "green",
                    "title": {"tag": "plain_text", "content": "已提交"},
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "你的操作已提交, 请查看本会话中的处理结果。",
                    }
                ],
            }
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
        chunks: list[InputChunk] = [
            TextChunk(
                _card_action_context(
                    event,
                    snapshot=snapshot,
                    card=original_card,
                    snapshot_status=snapshot_status,
                )
            )
        ]
        await stream_reply(
            channel,
            core,
            chat_id,
            chunks,
            reply_to=message_id or None,
            suppress_silent_reply=True,
        )
        logger.debug("card action stream completed")
    except Exception as e:
        logger.error(f"Card action handling error — {e!r}")
        if not chat_id:
            return
        try:
            await channel.send(chat_id, {"text": f"Error: {e}"})
        except Exception as notify_error:
            logger.error(f"Card action error notification failed — {notify_error!r}")
