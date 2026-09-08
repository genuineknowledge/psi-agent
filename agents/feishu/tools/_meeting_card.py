"""Meeting summary card: deterministic card payload + idempotent card delivery.

会议投递的卡片形态(格式形状归 ``skills/card-dsl/templates/meeting-summary-card.xml``,
本模块只做确定性组装):
  - 把 pipeline 的三段分析文本(meeting_summary / analysis_text /
    positive_negative_overview)解析、裁剪并渲染成模板 values;
  - ``render_meeting_summary_card`` 经 ``_card_dsl.render_template`` 走与
    review/todo 卡相同的模板渲染链路,返回 card 2.0 JSON;
  - ``notify_meeting_card`` 是文本 ``meeting_session_notify`` 的卡片孪生:
    同一收据文件与幂等 key (record_file_id + identity),同一 already_sent 语义,
    单发(不分块),群聊 oc_ 与私聊 ou_ 均由 send_card_impl 按前缀自动判型。
文本通知保持原样;本模块与文本通知写同一收据,同 record 只会有一种载荷成功发出。
"""

from __future__ import annotations

import ast
import hashlib
import json

import _card_dsl
import _feishu_impl as _f
from _meeting_automation import meeting_artifact_root
from meeting_session_notify import _read_receipts, _receipt_lock, _resolve_recipient, _write_receipts

from psi_agent._appdata import resolve_appdata_root

# 卡片各分区的字符上限(飞书 markdown 单元素有隐性大小约束,控制总量)。
_SUMMARY_LIMIT = 1200
_KEY_POINTS_LIMIT = 2200
_EVIDENCE_LIMIT = 160
_CANDIDATE_LIMIT = 12
_UNSTRUCTURED_OVERVIEW_LIMIT = 400
_ELLIPSIS = "\n\n…(内容较长, 已截断, 完整文本见会议存档)"

_CANDIDATE_KEYS = ("positive_candidates", "negative_candidates", "positives", "negatives")


def _truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _ELLIPSIS


def _parse_overview(text: str) -> dict[str, object] | None:
    """把 positive_negative_overview 解析成 dict。

    模型可能给 JSON 或 Python repr(dict); 两种都试, 都失败返回 None
    (调用方按"未结构化"处理, 不抛错)。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            value = loader(raw)
        except ValueError, SyntaxError:
            continue
        return value if isinstance(value, dict) else None
    return None


def _candidate_lines(candidates: object) -> str:
    lines: list[str] = []
    items = candidates if isinstance(candidates, list) else []
    for item in items[:_CANDIDATE_LIMIT]:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("candidate") or item.get("text") or "").strip()
        if not candidate:
            continue
        prefix = ""
        item_id = str(item.get("id") or "").strip()
        if item_id:
            prefix += f"{item_id} "
        scope = str(item.get("scope") or "").strip()
        if scope:
            prefix += f"({scope}) "
        line = f"- {prefix}{candidate}" if prefix else f"- {candidate}"
        evidence = str(item.get("evidence") or "").strip()
        if evidence:
            line += f"\n  证据: {_truncate(evidence, _EVIDENCE_LIMIT)}"
        kind = str(item.get("evidence_kind") or item.get("kind") or "").strip()
        if kind:
            line += f"\n  证据类型: {_truncate(kind, 80)}"
        lines.append(line)
    if len(items) > _CANDIDATE_LIMIT:
        lines.append(f"\n…另有 {len(items) - _CANDIDATE_LIMIT} 条候选, 完整清单见会议存档")
    return "\n".join(lines)


def _overview_sections(overview_text: str) -> tuple[str, str, str]:
    """从 overview 文本分出 (positive_md, negative_md, declaration_note)。

    declaration 等说明文字放进 note, 由调用方拼进 footer。
    """
    parsed = _parse_overview(overview_text)
    if parsed is None:
        # 未结构化: 原文不放正文区(避免破坏分区语义), 截断进 note 供追溯。
        return "", "", _truncate(overview_text, _UNSTRUCTURED_OVERVIEW_LIMIT)
    declaration = str(parsed.get("declaration") or "").strip()
    note = _truncate(declaration, 240) if declaration else ""
    positives = ""
    negatives = ""
    for key in _CANDIDATE_KEYS:
        if key in ("positive_candidates", "positives"):
            if not positives and parsed.get(key) is not None:
                positives = _candidate_lines(parsed.get(key))
        else:
            if not negatives and parsed.get(key) is not None:
                negatives = _candidate_lines(parsed.get(key))
    return positives, negatives, note


def render_meeting_summary_card(
    meeting_title: str,
    meeting_code: str,
    meeting_date: str,
    analysis: dict[str, str],
) -> dict[str, object]:
    """组装会议总结卡: 模板 + values → card 2.0 JSON。

    Returns ``{"ok": True, "card": {...}, "handlers": {...}, "values": {...}}``
    or ``{"ok": False, "error": ...}`` — the same shape the render tool uses.
    """
    summary = str(analysis.get("meeting_summary") or "").strip()
    key_points = str(analysis.get("analysis_text") or "").strip()
    overview_text = str(analysis.get("positive_negative_overview") or "").strip()
    positives, negatives, overview_note = _overview_sections(overview_text)
    footer = f"⚠️ 候选观察: 不进入正式正负面总表 · 不计分 · 不进入绩效 | 会议号 {meeting_code}"
    if overview_note:
        footer = f"{overview_note}\n\n{footer}"
    values = {
        "meeting_line": f"{meeting_title} · {meeting_date}",
        "summary": _truncate(summary, _SUMMARY_LIMIT),
        "key_points": _truncate(key_points, _KEY_POINTS_LIMIT),
        "positives": positives,
        "negatives": negatives,
        "footer": footer,
    }
    rendered = _card_dsl.render_template("meeting-summary-card", values_json=json.dumps(values, ensure_ascii=False))
    if not rendered.get("ok"):
        return {"ok": False, "error": str(rendered.get("error") or "card render failed")}
    return {"ok": True, "card": rendered["card"], "handlers": rendered.get("handlers", {}), "values": values}


async def notify_meeting_card(
    meeting_name: str,
    recipient: str,
    card_json: str,
    record_file_id: str = "",
    user_key: str = "",
    appdata_root: str = "",
) -> str:
    """向固定个人或群聊收件人发送会议总结卡, 并记录幂等回执(与文本通知同文件)。"""
    try:
        if not meeting_name.strip() or not recipient.strip() or not card_json.strip():
            raise ValueError("meeting_name, recipient, and card_json are required")
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        artifact.mkdir(parents=True, exist_ok=True)
        identity, display_name = await _resolve_recipient(recipient, user_key)
        if not identity:
            return json.dumps(
                {"ok": False, "status": "recipient_unresolved", "recipient": recipient, "error": display_name},
                ensure_ascii=False,
            )
        receipt_path = artifact / "notification_receipts.json"
        receipt_key = hashlib.sha256(f"{record_file_id}\n{identity}".encode()).hexdigest()
        async with await _receipt_lock(str(receipt_path) + ":" + receipt_key):
            receipts = await _read_receipts(receipt_path)
            previous = receipts.get(receipt_key)
            if isinstance(previous, dict) and previous.get("ok"):
                return json.dumps(
                    {
                        "ok": True,
                        "status": "already_sent",
                        "recipient": display_name,
                        "message_id": previous.get("message_id", ""),
                    },
                    ensure_ascii=False,
                )
            delivery = "direct" if identity.startswith("ou_") else "chat"
            content_hash = hashlib.sha256(card_json.encode()).hexdigest()
            if isinstance(previous, dict) and previous.get("text_sha256") not in (None, "", content_hash):
                return json.dumps(
                    {
                        "ok": False,
                        "status": "receipt_content_mismatch",
                        "recipient": display_name,
                        "delivery": delivery,
                        "recipient_id": identity,
                        "message_ids": previous.get("message_ids", []),
                        "error": "同 record 已有不同内容的通知, 拒绝覆盖发送",
                    },
                    ensure_ascii=False,
                )
            try:
                sent = await _f.send_card_impl(identity, card_json, "open_id", user_key or None)
            except Exception as exc:
                sent = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            if not isinstance(sent, dict) or not sent.get("ok"):
                error = str((sent or {}).get("message") or "card send failed")
                receipt = {
                    "ok": False,
                    "status": "send_failed",
                    "recipient": display_name,
                    "delivery": delivery,
                    "recipient_id": identity,
                    "message_id": "",
                    "message_ids": [],
                    "text_sha256": content_hash,
                    "error": error,
                }
                receipts[receipt_key] = receipt
                await _write_receipts(receipt_path, receipts)
                return json.dumps(receipt, ensure_ascii=False)
            message_id = str(sent.get("message_id") or "")
            receipt = {
                "ok": True,
                "status": "sent",
                "recipient": display_name,
                "delivery": delivery,
                "recipient_id": identity,
                "message_id": message_id,
                "message_ids": [message_id] if message_id else [],
                "text_sha256": content_hash,
                "error": "",
            }
            receipts[receipt_key] = receipt
            await _write_receipts(receipt_path, receipts)
            return json.dumps(receipt, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_card_notification_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["notify_meeting_card", "render_meeting_summary_card"]
