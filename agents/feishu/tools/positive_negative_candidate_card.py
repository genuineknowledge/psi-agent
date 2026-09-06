# ruff: noqa: E402, E501, RUF001
"""Meeting-note candidate整理卡.

This card never writes a ledger row.  It only groups, ignores, or marks
candidate evidence for follow-up; the existing case confirmation tool remains
the sole write gate for the robot test table.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

TOOLS_DIR = __import__("pathlib").Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _assignment_display import readable_name
from _positive_negative_list import candidate_batches


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
    merge_buttons = []
    for target in batch.get("rows") or []:
        target_index = int(target.get("index", -1))
        if target_index == index or target.get("status") in {"merged", "ignored"}:
            continue
        merge_buttons.append(
            _button(
                f"合并到{target_index + 1}",
                f"pn_candidate_merge_{index}_{target_index}",
                base,
            )
        )
    if not merge_buttons:
        merge_buttons = [_button("合并到…", f"pn_candidate_merge_pick_{index}", base)]
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
            {"tag": "column", "width": "weighted", "weight": 1, "elements": merge_buttons},
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [_button("补充证据", f"pn_candidate_evidence_{index}", base)],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [_button("忽略", f"pn_candidate_ignore_{index}", base)],
            },
        ],
    }


def _evidence_form(batch: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "补充候选证据"}, "template": "orange"},
        "elements": [
            {
                "tag": "markdown",
                "content": f"**候选**：{row.get('text') or ''}\n请补充可观察行为和可追溯来源；提交后仍需回到候选整理卡继续判断。",
            },
            {
                "tag": "form",
                "name": "pn_candidate_evidence_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "observed_behavior",
                        "required": True,
                        "placeholder": {"tag": "plain_text", "content": "实际做了什么？"},
                    },
                    {
                        "tag": "input",
                        "name": "evidence_sources",
                        "required": True,
                        "placeholder": {"tag": "plain_text", "content": "聊天记录 / 任务记录 / 截图等"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "提交补充"},
                        "type": "primary",
                        "name": "submit_evidence",
                        "action_type": "form_submit",
                        "value": {
                            "action": "pn_candidate_evidence_submit",
                            "batch_id": batch["batch_id"],
                            "row_index": int(row["index"]),
                        },
                    },
                ],
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
            "content": "这些是待核实的行为线索，不是正式记录。请先合并同一事件、补证或忽略；完成整理后再进入分析和测试表确认。",
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
    if batch.get("status") == "ready_for_analysis":
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "整理完成，进入分析"},
                        "type": "primary",
                        "value": {"action": "pn_candidate_finalize", "batch_id": batch["batch_id"]},
                    }
                ],
            }
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
    match = re.fullmatch(r"pn_candidate_(keep|ignore|evidence|merge_pick)_(\d+)", action)
    if match:
        return match.group(1), int(match.group(2)), None
    match = re.fullmatch(r"pn_candidate_merge_(\d+)_(\d+)", action)
    if match:
        return "merge", int(match.group(1)), int(match.group(2))
    return None


async def _load_candidate_batch(batch_id: str) -> dict[str, Any] | None:
    return await candidate_batches.load_batch(batch_id)


async def _handle_click(card_action_json: str, user_key: str) -> str:
    action = _parse(card_action_json)
    batch_id = str(action.get("batch_id") or "")
    batch = await candidate_batches.load_batch(batch_id)
    if not batch:
        return _f.dumps_result({"ok": False, "status": "candidate_batch_not_found"})
    if batch.get("person_open_id") != user_key and user_key:
        return _f.dumps_result({"ok": False, "status": "unauthorized"})
    if str(action.get("action") or "") == "pn_candidate_finalize":
        if batch.get("status") != "ready_for_analysis":
            return _f.dumps_result({"ok": False, "status": "candidate_batch_not_ready"})
        active = candidate_batches.analysis_candidates(batch)
        if not active:
            return _f.dumps_result({"ok": False, "status": "no_candidate_to_analyze"})
        batch["status"] = "analysis_started"
        await candidate_batches.save_batch(batch)
        message_id = str(action.get("_message_id") or batch.get("message_id") or "")
        if message_id:
            await _f.edit_card_impl(message_id, json.dumps(render_candidate_card(batch), ensure_ascii=False), user_key)
        return _f.dumps_result(
            {
                "ok": True,
                "status": "analysis_started",
                "batch_id": batch_id,
                "analysis_candidates": active,
                "candidates": active,
            }
        )
    if str(action.get("action") or "") == "pn_candidate_evidence_submit":
        row_index = int(action.get("row_index", -1))
        rows = batch.get("rows") or []
        if not (0 <= row_index < len(rows)):
            return _f.dumps_result({"ok": False, "status": "candidate_row_not_found"})
        form = action.get("_form_value") if isinstance(action.get("_form_value"), dict) else {}
        observed = str(form.get("observed_behavior") or "").strip()
        sources = [part.strip() for part in str(form.get("evidence_sources") or "").split(",") if part.strip()]
        if not observed or not sources:
            return _f.dumps_result({"ok": False, "status": "candidate_evidence_incomplete"})
        row = rows[row_index]
        row["text"] = observed
        row["source_candidates"] = list(dict.fromkeys([*(row.get("source_candidates") or []), observed]))
        row["evidence_sources"] = sources
        row["quality_status"] = "candidate"
        row["quality_reason"] = "已补充可观察行为和证据来源"
        row["status"] = "pending"
        row["decided_at"] = ""
        batch["status"] = "pending"
        await candidate_batches.save_batch(batch)
        return _f.dumps_result({"ok": True, "status": "evidence_saved", "batch_id": batch_id, "row_index": row_index})
    parsed = _action_row_index(str(action.get("action") or ""))
    if not parsed:
        return _f.dumps_result({"ok": False, "status": "invalid_candidate_action"})
    kind, source_index, target_index = parsed
    rows = batch.get("rows") or []
    if not (0 <= source_index < len(rows)):
        return _f.dumps_result({"ok": False, "status": "candidate_row_not_found"})
    row = rows[source_index]
    if kind == "keep":
        if row.get("quality_status") == "needs_observable_behavior":
            return _f.dumps_result({"ok": False, "status": "needs_observable_behavior", "row_index": source_index})
        if row.get("status") == "pending":
            row["status"] = "kept"
            row["decided_at"] = candidate_batches._now()
    elif kind == "ignore":
        if row.get("status") == "pending":
            row["status"] = "ignored"
            row["decided_at"] = candidate_batches._now()
    elif kind == "evidence":
        if row.get("status") == "pending":
            row["status"] = "needs_evidence"
            row["decided_at"] = candidate_batches._now()
        form_result = await _f.send_card_impl(
            batch["person_open_id"],
            json.dumps(_evidence_form(batch, row), ensure_ascii=False),
            "open_id",
            user_key,
            json.dumps({"kind": "pn_candidate_evidence", "batch_id": batch_id}, ensure_ascii=False),
            json.dumps({"pn_candidate_evidence_submit": "positive_negative_candidate_card"}, ensure_ascii=False),
            False,
        )
        await candidate_batches.save_batch(batch)
        return _f.dumps_result(
            {
                "ok": bool(isinstance(form_result, dict) and form_result.get("ok")),
                "status": "evidence_form_sent",
                "batch_id": batch_id,
                "row_index": source_index,
                "message_id": str((form_result or {}).get("message_id") or "")
                if isinstance(form_result, dict)
                else "",
            }
        )
    elif kind == "merge":
        candidate_batches.merge_candidate(batch, source_index, int(target_index))
    elif kind == "merge_pick":
        return _f.dumps_result({"ok": True, "status": "choose_merge_target", "row_index": source_index})
    if candidate_batches.is_ready_for_analysis(batch):
        batch["status"] = "ready_for_analysis"
    await candidate_batches.save_batch(batch)
    message_id = str(action.get("_message_id") or batch.get("message_id") or "")
    if message_id:
        await _f.edit_card_impl(message_id, json.dumps(render_candidate_card(batch), ensure_ascii=False), user_key)
    return _f.dumps_result(
        {"ok": True, "status": batch["status"], "action": action.get("action"), "batch_id": batch_id}
    )


def candidate_card_handlers(batch: dict[str, Any]) -> dict[str, str]:
    handlers: dict[str, str] = {"pn_candidate_finalize": "positive_negative_candidate_card"}
    rows = batch.get("rows") or []
    for row in rows:
        if row.get("status") != "pending":
            continue
        index = int(row.get("index", -1))
        handlers[f"pn_candidate_keep_{index}"] = "positive_negative_candidate_card"
        handlers[f"pn_candidate_evidence_{index}"] = "positive_negative_candidate_card"
        handlers[f"pn_candidate_ignore_{index}"] = "positive_negative_candidate_card"
        for target in rows:
            target_index = int(target.get("index", -1))
            if target_index != index and target.get("status") not in {"merged", "ignored"}:
                handlers[f"pn_candidate_merge_{index}_{target_index}"] = "positive_negative_candidate_card"
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
    if source_key:
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
    candidates = [item if isinstance(item, dict) else {"text": item} for item in raw]
    batch = candidate_batches.build_candidate_batch(
        person_open_id=receive_id,
        person_name=person_name,
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
