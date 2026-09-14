"""Prepare a writer-confirmed positive-negative-list case from a private chat."""

# ruff: noqa: E402, RUF001

from __future__ import annotations

import hashlib
import json
import secrets
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _assignment_display import (
    readable_name,
    render_people_display,
    resolve_feishu_display_names,
    resolve_people_display,
)
from _positive_negative_list.dedupe import (
    build_cross_source_fingerprint,
    make_source_key,
    release_source_key,
    reserve_source_key,
)
from _positive_negative_list.drafts import delete_draft_body, save_draft
from _positive_negative_list.models import CaseDraft
from _positive_negative_list.validation import validate_case
from loguru import logger

from psi_agent._appdata import resolve_appdata_root as _resolve_appdata_root
from psi_agent.session.runtime_context import get_session_id as _get_session_id

# The exact case_json surface the skill contract exposes to the model.
# Anything else (workflow, case ids, dedupe identifiers, source-session
# bookkeeping, red-line state, record links) is generated or decided by the
# tools and must not be supplied by the model.
_CASE_JSON_ALLOWED_FIELDS = frozenset(
    {
        "writer_user_key",
        "reporter_user_key",
        "subject_user_key",
        "occurred_at",
        "observed_behavior",
        "context",
        "impact",
        "evidence_sources",
        "nature",
        "category",
        "primary_rule_id",
        "secondary_rule_ids",
        "rule_version",
        "fact_summary",
        "agent_inference",
        "correct_behavior",
        "immediate_remedy",
        "prevention",
    }
)


def _preview_digest(case: CaseDraft) -> str:
    # ``workflow`` changes while a card is being written and is not part of
    # the user-visible confirmation snapshot.
    snapshot = case.to_mapping()
    snapshot.pop("workflow", None)
    body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


async def _confirmation_card(case: CaseDraft, digest: str) -> dict[str, Any]:
    nature = {"positive": "正面行为", "negative": "负面行为"}.get(case.nature, case.nature)
    subject_display = await resolve_people_display(case.subject_user_key, _f.get_users_batch_impl)
    guidance = ""
    if case.nature == "negative":
        guidance = (
            f"\n**正确做法 · 建议**　{case.correct_behavior}"
            f"\n**立即补救**　{case.immediate_remedy}"
            f"\n**预防措施**　{case.prevention}"
        )
    action_value = {"action": "positive_negative_case_confirm", "case_id": case.case_id, "preview_digest": digest}
    cancel_value = {"action": "positive_negative_case_cancel", "case_id": case.case_id, "preview_digest": digest}
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "正负面清单 · 记录确认"}},
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**对象**　{subject_display} · {nature}\n"
                        f"**发生时间**　{case.occurred_at}　·　**分类**　{case.category}\n"
                        f"**行为事实**　{case.fact_summary}\n"
                        f"**证据来源**　{', '.join(case.evidence_sources) or '未提供'}"
                        f"{guidance}\n\n确认后仅写入正负面清单正式总表；不计分、不进入绩效。"
                        "取消录入不写入任何表，可重新发起。"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "确认写入"},
                                    "type": "primary",
                                    "behaviors": [{"type": "callback", "value": action_value}],
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "取消录入"},
                                    "type": "default",
                                    "behaviors": [{"type": "callback", "value": cancel_value}],
                                }
                            ],
                        },
                    ],
                },
            ]
        },
    }


async def _public_case_preview(case: CaseDraft) -> dict[str, Any]:
    """Return a confirmation preview without exposing storage identities."""
    identities = {
        part.strip()
        for value in (case.writer_user_key, case.reporter_user_key, case.subject_user_key)
        for part in value.replace("，", ",").split(",")
        if readable_name(part.strip()) is None
    }
    names = await resolve_feishu_display_names(identities, _f.get_users_batch_impl)

    nature = {"positive": "正面行为", "negative": "负面行为"}.get(case.nature, case.nature)
    return {
        "写入者": render_people_display(case.writer_user_key, names),
        "报告人": render_people_display(case.reporter_user_key, names),
        "涉事人": render_people_display(case.subject_user_key, names),
        "发生时间": case.occurred_at,
        "观察到的行为": case.observed_behavior,
        "场合/背景": case.context,
        "影响": case.impact,
        "证据来源": list(case.evidence_sources),
        "行为性质": nature,
        "分类": case.category,
        "行为事实": case.fact_summary,
        "判断说明": case.agent_inference,
        "正确做法": case.correct_behavior,
        "立即补救": case.immediate_remedy,
        "预防措施": case.prevention,
    }


def _case_has_durable_state(root: str | Path, case_id: str) -> bool:
    """True when a case still owns a draft or a write receipt on disk."""
    base = Path(root) / "positive-negative-list"
    if (base / "receipts" / f"{case_id}.json").is_file():
        return True
    drafts_root = base / "drafts"
    if not drafts_root.is_dir():
        return False
    for writer_dir in drafts_root.iterdir():
        if writer_dir.is_dir() and (writer_dir / f"{case_id}.json").is_file():
            return True
    return False


async def positive_negative_case_prepare(
    case_json: str,
    source_type: str = "feishu_private_chat",
    source_event_id: str = "",
    source_message_id: str = "",
    user_key: str = "",
) -> str:
    if source_type != "feishu_private_chat" or not user_key.strip():
        return _f.dumps_result({"ok": False, "error": "一期只接受可信的人工飞书私聊身份"})
    try:
        raw: dict[str, Any] = json.loads(case_json)
        if not isinstance(raw, dict):
            raise ValueError("case_json must be an object")
        internal = set(raw) - _CASE_JSON_ALLOWED_FIELDS
        if internal:
            red_line_flag = "red_line_candidate" in internal and raw.get("red_line_candidate") not in (None, False)
            supplied_link = str(raw.get("record_link") or "").strip() if "record_link" in internal else ""
            if red_line_flag:
                return _f.dumps_result(
                    {
                        "ok": False,
                        "status": "red_line_state_rejected",
                        "error": "红线候选状态只能由人工处理流程认定，不接受模型传入 red_line_candidate",
                        "allowed_case_fields": sorted(_CASE_JSON_ALLOWED_FIELDS),
                    }
                )
            if supplied_link:
                return _f.dumps_result(
                    {
                        "ok": False,
                        "status": "record_link_rejected",
                        "error": "记录链接由工具在写入后生成，不接受模型传入 record_link",
                        "allowed_case_fields": sorted(_CASE_JSON_ALLOWED_FIELDS),
                    }
                )
            # Tool-owned keys (workflow/case_id/dedupe identifiers/source
            # bookkeeping) are regenerated below; strip any model-supplied
            # values so the skill contract stays the only way in.
            raw = {key: value for key, value in raw.items() if key in _CASE_JSON_ALLOWED_FIELDS}
        supplied_writer = raw.get("writer_user_key")
        if supplied_writer not in (None, "", user_key):
            raise ValueError("writer identity does not match trusted sender")
        raw["writer_user_key"] = user_key
        raw["workflow"] = "ready_for_confirmation"
        raw["source_type"] = source_type
        raw["source_event_id"] = source_event_id
        raw["source_message_id"] = source_message_id
        raw["source_session_id"] = _get_session_id()
        case = CaseDraft.from_mapping(raw)
        errors = validate_case(case)
        if errors:
            return _f.dumps_result({"ok": False, "errors": list(errors)})
        source_key = make_source_key(source_type, source_event_id or None, source_message_id or None)
        # This fingerprint is private runtime state used only to surface a
        # possible duplicate before writing the isolated test table.  Derive a
        # stable secret from the already configured Feishu application and the
        # AppData root, so deploying this skill does not introduce another
        # configuration field.
        root = await _resolve_appdata_root()
        app_identity = str(_f._config() or "")
        dedupe_secret = hashlib.sha256(f"{root}\0{app_identity}\0positive-negative-list".encode()).digest()
        fingerprint = build_cross_source_fingerprint(case, dedupe_secret)
        case_id = f"case_{secrets.token_urlsafe(12)}"
        canonical_id = f"incident_{secrets.token_urlsafe(12)}"
        case = CaseDraft.from_mapping(
            case.to_mapping()
            | {
                "case_id": case_id,
                "source_key": source_key,
                "cross_source_fingerprint": fingerprint,
                "canonical_incident_id": canonical_id,
            }
        )
        reservation = reserve_source_key(root, source_key, case_id)
        if reservation.status == "exact_duplicate":
            # A reservation whose case has neither a draft nor a receipt was
            # left behind by a crash between reserve and card send.  Reclaim
            # it so the same source can be retried instead of being blocked
            # forever by an orphaned placeholder.
            orphan_case = reservation.case_id
            if orphan_case and not _case_has_durable_state(root, orphan_case):
                release_source_key(root, source_key, orphan_case)
                logger.warning(f"pnl prepare: reclaimed orphan reservation source={source_key} case={orphan_case}")
                reservation = reserve_source_key(root, source_key, case_id)
        if reservation.status not in {"reserved", "idempotent"}:
            return _f.dumps_result({"ok": False, "status": reservation.status, "case_id": reservation.case_id})
        session_id = _get_session_id()
        save_draft(root, user_key, session_id, case_id, case)
        digest = _preview_digest(case)
        card = await _confirmation_card(case, digest)
        try:
            sent = await _f.send_card_impl(
                user_key,
                json.dumps(card, ensure_ascii=False),
                "open_id",
                user_key,
                json.dumps(
                    {"case_id": case_id, "preview_digest": digest, "writer_open_id": user_key}, ensure_ascii=False
                ),
                json.dumps(
                    {
                        "positive_negative_case_confirm": "positive_negative_case_confirm",
                        "positive_negative_case_cancel": "positive_negative_case_confirm",
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:
            delete_draft_body(root, user_key, case_id)
            release_source_key(root, source_key, case_id)
            return _f.dumps_result({"ok": False, "status": "confirmation_card_failed", "error": str(exc)})
        if not isinstance(sent, dict) or not sent.get("ok"):
            delete_draft_body(root, user_key, case_id)
            release_source_key(root, source_key, case_id)
            message = sent.get("message") if isinstance(sent, dict) else "确认卡发送失败"
            return _f.dumps_result({"ok": False, "status": "confirmation_card_failed", "error": message})
        logger.info(f"pnl prepare: confirmation card sent case={case_id} source={source_key}")
        return _f.dumps_result(
            {
                "ok": True,
                "status": "待写入者确认",
                "case_id": case_id,
                "rule_version": case.rule_version,
                "message_id": sent.get("message_id", ""),
                "confirmation_scope": "写入正负面清单正式总表",
                "preview_digest": digest,
                "preview": await _public_case_preview(case),
            }
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        allowed_case_fields = [field.name for field in fields(CaseDraft)]
        return _f.dumps_result(
            {
                "ok": False,
                "error": str(exc),
                "allowed_case_fields": allowed_case_fields,
                "hint": "case_json 只能使用上述候选记录字段；展示姓名与飞书 open_id 不是同一字段。",
            }
        )
