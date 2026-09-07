# ruff: noqa: E402, E501, RUF001
"""Meeting-note candidate整理卡.

This card never writes a ledger row.  It only keeps or ignores
candidate evidence for follow-up; the existing case confirmation tool remains
the sole write gate for the public ledger.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any

TOOLS_DIR = __import__("pathlib").Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _assignment_display import readable_name, resolve_people_display
from _positive_negative_list import candidate_batches

# Per-batch in-process lock: decision clicks do a whole-batch read-modify-write,
# and concurrent clicks on different rows of the same batch must not lose each
# other's decision.
_CLICK_LOCKS: dict[str, asyncio.Lock] = {}


def _parse(value: str) -> dict[str, Any]:
    try:
        raw = json.loads(value) if value.strip() else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    action = raw.get("action")
    if not isinstance(action, dict):
        return {}
    payload = action.get("value")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    result = dict(payload) if isinstance(payload, dict) else {}
    result["_message_id"] = str(raw.get("message_id") or "")
    operator = raw.get("operator")
    result["_operator"] = str(operator.get("open_id") or "") if isinstance(operator, dict) else ""
    form_value = action.get("form_value")
    if isinstance(form_value, dict):
        result["_form_value"] = form_value
    return result


def _display(value: str) -> str:
    return value if readable_name(value) else "姓名未解析"


def _button(label: str, action: str, value: dict[str, Any], button_type: str = "default") -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "behaviors": [{"type": "callback", "value": {**value, "action": action}}],
    }


def _row_actions(batch: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    index = int(row["index"])
    base = {"batch_id": batch["batch_id"], "row_index": index, "person_open_id": batch["person_open_id"]}
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [_button("纳入候选", f"pn_candidate_keep_{index}", base, "primary")],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [_button("暂时忽略", f"pn_candidate_ignore_{index}", base)],
            },
        ],
    }


def render_candidate_card(batch: dict[str, Any]) -> dict[str, Any]:
    rows = batch.get("rows") or []
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**正负面清单 · 候选整理**\n来源：{batch.get('source_label') or '会议纪要'} · {batch.get('meeting_date') or '日期待补'} · 对象：{_display(str(batch.get('person_name') or ''))}",
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "这些是待核实的行为线索，不是正式记录。请先选择纳入候选或暂时忽略；缺少的事实会在后续分析对话中轻量补问。",
        },
    ]
    for row in rows:
        status = row.get("status")
        if status == "merged":
            elements.append(
                {
                    "tag": "markdown",
                    "content": f"↪️ {row.get('index', 0) + 1}. 已合并到事件 {int(row.get('merged_into') or 0) + 1}",
                }
            )
            continue
        if status == "ignored":
            elements.append(
                {"tag": "markdown", "content": f"➖ {row.get('index', 0) + 1}. 已忽略：{row.get('text') or ''}"}
            )
            continue
        if status == "kept":
            elements.append(
                {
                    "tag": "markdown",
                    "content": f"✅ {row.get('index', 0) + 1}. 已纳入候选：{row.get('text') or ''}",
                }
            )
            continue
        quality = row.get("quality_reason") or ""
        elements.append(
            {
                "tag": "markdown",
                "content": f"**{row.get('index', 0) + 1}. {row.get('text') or ''}**\n<font color='#8F959E'>{quality}</font>",
            }
        )
        elements.append(_row_actions(batch, row))
    pending = sum(1 for row in rows if row.get("status") == "pending")
    elements.extend(
        [
            {"tag": "hr"},
            {"tag": "markdown", "content": f"待整理：{pending} 条 · 当前不写入任何表格"},
        ]
    )
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {
            "title": {"tag": "plain_text", "content": "正负面清单 · 候选整理"},
            "template": "blue" if pending else "grey",
        },
        "body": {"elements": elements},
    }


def _action_row_index(action: str) -> tuple[str, int, int | None] | None:
    match = re.fullmatch(r"pn_candidate_(keep|ignore)_(\d+)", action)
    if match:
        return match.group(1), int(match.group(2)), None
    return None


async def _load_candidate_batch(batch_id: str) -> dict[str, Any] | None:
    return await candidate_batches.load_batch(batch_id)


async def _handle_click(card_action_json: str, user_key: str) -> str:
    action = _parse(card_action_json)
    batch_id = str(action.get("batch_id") or "")
    batch = await candidate_batches.load_batch(batch_id)
    if not batch:
        return _f.dumps_result({"ok": False, "status": "candidate_batch_not_found"})
    if not user_key or batch.get("person_open_id") != user_key:
        return _f.dumps_result({"ok": False, "status": "unauthorized"})
    action_name = str(action.get("action") or "")
    if action_name.startswith("pn_candidate_") and _action_row_index(action_name) is None:
        return _f.dumps_result({"ok": False, "status": "unsupported_legacy_action"})
    parsed = _action_row_index(str(action.get("action") or ""))
    if not parsed:
        return _f.dumps_result({"ok": False, "status": "invalid_candidate_action"})
    kind, source_index, _ = parsed

    lock = _CLICK_LOCKS.setdefault(batch_id, asyncio.Lock())
    async with lock:
        # Reload under the batch lock so concurrent clicks on different rows of
        # the same batch cannot overwrite each other's decision.
        batch = await candidate_batches.load_batch(batch_id)
        if not batch:
            return _f.dumps_result({"ok": False, "status": "candidate_batch_not_found"})
        rows = batch.get("rows") or []
        if not (0 <= source_index < len(rows)):
            return _f.dumps_result({"ok": False, "status": "candidate_row_not_found"})
        row = rows[source_index]
        current = str(row.get("status") or "pending")
        changed = False
        if kind == "keep" and current == "pending":
            row["status"] = "kept"
            row["decided_at"] = candidate_batches._now()
            changed = True
        elif kind == "ignore" and current == "pending":
            row["status"] = "ignored"
            row["decided_at"] = candidate_batches._now()
            changed = True
        if changed and candidate_batches.is_ready_for_analysis(batch):
            batch["status"] = "ready_for_analysis"
        if changed:
            await candidate_batches.save_batch(batch)
            message_id = str(action.get("_message_id") or batch.get("message_id") or "")
            if message_id:
                await _f.edit_card_impl(
                    message_id, json.dumps(render_candidate_card(batch), ensure_ascii=False), user_key
                )
        result: dict[str, Any] = {
            "ok": True,
            "status": batch["status"] if changed else "already_decided",
            "row_status": row["status"],
            "action": action.get("action"),
            "batch_id": batch_id,
        }
        if batch["status"] == "ready_for_analysis":
            active = candidate_batches.analysis_candidates(batch)
            result.update({"analysis_candidates": active, "candidates": active})
        return _f.dumps_result(result)


def candidate_card_handlers(batch: dict[str, Any]) -> dict[str, str]:
    handlers: dict[str, str] = {}
    rows = batch.get("rows") or []
    for row in rows:
        if row.get("status") != "pending":
            continue
        index = int(row.get("index", -1))
        handlers[f"pn_candidate_keep_{index}"] = "positive_negative_candidate_card"
        handlers[f"pn_candidate_ignore_{index}"] = "positive_negative_candidate_card"
    return handlers


async def positive_negative_candidate_card(
    receive_id: str = "",
    candidates_json: str = "",
    person_name: str = "",
    source_label: str = "会议纪要",
    meeting_date: str = "",
    source_key: str = "",
    user_key: str = "",
    card_action_json: str = "",
) -> str:
    if card_action_json.strip():
        return await _handle_click(card_action_json, user_key)
    if not receive_id.strip() or not user_key.strip() or receive_id.strip() != user_key.strip():
        return _f.dumps_result({"ok": False, "status": "candidate_identity_required"})
    try:
        raw = json.loads(candidates_json) if candidates_json.strip() else []
    except json.JSONDecodeError as exc:
        return _f.dumps_result({"ok": False, "status": "invalid_candidates", "error": str(exc)})
    if not isinstance(raw, list) or not raw:
        return _f.dumps_result({"ok": False, "status": "no_candidates"})
    candidates = [item if isinstance(item, dict) else {"text": item} for item in raw]
    if not source_key:
        # Same-source idempotency without a caller-provided key: an identical
        # payload for the same person derives the same key and never sends a
        # second candidate card.
        source_key = candidate_batches.derive_source_key(
            source_label=source_label,
            meeting_date=meeting_date,
            person_open_id=receive_id,
            candidates=candidates,
        )
    existing = await candidate_batches.find_batch_by_source_key(source_key)
    if existing:
        return _f.dumps_result(
            {
                "ok": True,
                "status": "already_sent",
                "batch_id": existing["batch_id"],
                "message_id": existing.get("message_id", ""),
            }
        )
    display_person_name = person_name.strip()
    if not readable_name(display_person_name):
        display_person_name = await resolve_people_display(display_person_name, _f.get_users_batch_impl)
    batch = candidate_batches.build_candidate_batch(
        person_open_id=receive_id,
        person_name=display_person_name,
        source_label=source_label,
        meeting_date=meeting_date,
        candidates=candidates,
        source_key=source_key,
    )
    await candidate_batches.save_batch(batch)
    sent = await _f.send_card_impl(
        receive_id,
        json.dumps(render_candidate_card(batch), ensure_ascii=False),
        "open_id",
        user_key,
        json.dumps({"kind": "pn_candidate整理", "batch_id": batch["batch_id"]}, ensure_ascii=False),
        json.dumps(candidate_card_handlers(batch), ensure_ascii=False),
        True,
    )
    if not isinstance(sent, dict) or not sent.get("ok"):
        return _f.dumps_result({"ok": False, "status": "candidate_card_send_failed"})
    batch["message_id"] = str(sent.get("message_id") or "")
    await candidate_batches.save_batch(batch)
    return _f.dumps_result(
        {
            "ok": True,
            "status": "sent",
            "batch_id": batch["batch_id"],
            "message_id": batch["message_id"],
            "counts": {"candidates": len(batch["rows"])},
        }
    )


__all__ = ["positive_negative_candidate_card"]
