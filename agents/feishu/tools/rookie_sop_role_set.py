"""新人在开发环境卡上自选角色: 非研发则整模块标不适用, 研发则展开 5 项。

刻意为之: 选「研发」时发一张新卡而不是 edit 原卡 —— 原卡的角色按钮点完就被消费了,
edit_card 不重新注册回调, 编辑出来的勾选按钮全是死的。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_edit_card

from psi_agent.channel.feishu._card_store import rewrite_card_snapshot as _rewrite_card_snapshot

_HANDLER = "rookie_sop_role_set"
_DEV_MODULE = "开发环境"
_ROLE_CONFIRMED_ITEM = "role_confirmed"


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _resolve_role(payload: dict[str, Any]) -> dict[str, Any]:
    dispatch = _as_dict(payload.get("dispatch"))
    handler = str(dispatch.get("handler") or "").strip()
    if handler and handler != _HANDLER:
        return {"error": f"unexpected handler {handler!r}; expected {_HANDLER!r}"}
    if handler == _HANDLER and dispatch.get("matched") is False:
        return {"error": "dispatch.matched is false; do not invent a handler"}

    action = _as_dict(payload.get("action"))
    value = action.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    value = _as_dict(value)

    action_name = str(value.get("action") or action.get("action_id") or "")
    role = str(value.get("role") or "").strip()
    if not role:
        if action_name == _card.ACTION_ROLE_DEV:
            role = "dev"
        elif action_name == _card.ACTION_ROLE_NONDEV:
            role = "nondev"
    if role not in {"dev", "nondev"}:
        return {"error": f"cannot resolve role from action {action_name!r}"}

    business = _as_dict(payload.get("business_context"))
    source = _as_dict(payload.get("source"))
    return {
        "error": "",
        "role": role,
        "open_id": str(business.get("open_id") or "").strip()
        or str(source.get("operator_open_id") or source.get("open_id") or "").strip(),
        "name": str(business.get("name") or "").strip(),
        "app_token": str(business.get("app_token") or "").strip(),
        "detail_table_id": str(business.get("detail_table_id") or "").strip(),
        "overview_table_id": str(business.get("overview_table_id") or "").strip(),
        # 原地更新这张卡要用它(不再补发第二张)
        "message_id": str(payload.get("message_id") or "").strip(),
    }


async def rookie_sop_role_set(card_action_json: str = "") -> str:
    """Record the new hire's role from the 开发环境 card, then settle that module.

    Call this for a ``<feishu_card_action>`` whose ``dispatch.handler`` is
    ``rookie_sop_role_set``. Non-dev marks every 开发环境 row 不适用 (excluded from the
    progress denominator, reminders and the HR digest) and sends one terminal card.
    Dev sends a **new** card listing the five dev items — the original card's buttons were
    consumed on click and cannot be revived by editing. Finish with zero assistant content
    unless this tool reports an error; the sent card is the visible response.

    Args:
        card_action_json: Full ``<feishu_card_action>`` payload JSON string.
    """
    payload = _store._parse_result(card_action_json)
    if not payload:
        return json.dumps({"ok": False, "error": "card_action_json must be a JSON object"}, ensure_ascii=False)

    ctx = _resolve_role(payload)
    if ctx.get("error"):
        return json.dumps({"ok": False, "error": ctx["error"]}, ensure_ascii=False)
    if not ctx["open_id"]:
        return json.dumps({"ok": False, "error": "cannot resolve open_id"}, ensure_ascii=False)

    state = await _rt.load_state()
    app_token = ctx["app_token"] or str(state.get("app_token") or "")
    detail_table = ctx["detail_table_id"] or str(state.get("detail_table_id") or "")
    overview_table = ctx["overview_table_id"] or str(state.get("overview_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    today = date.today()
    is_dev = ctx["role"] == "dev"
    label = "研发" if is_dev else "非研发"

    rows, truncated = await _store.fetch_detail(bitable, app_token, detail_table, ctx["open_id"])
    # role_confirmed 同在 开发环境 模块但全员适用 —— 它不参与 适用角色 改写/不适用
    # 改写, 只走下面独立的 mark_done(需求 4: 它绝不能被种成/改成 不适用)。
    dev_rows = [
        r for r in rows if str(r.get("模块") or "") == _DEV_MODULE and _store._item_id_of(r) != _ROLE_CONFIRMED_ITEM
    ]
    label_update_error = ""
    if dev_rows:
        raw = await bitable.update_records(
            app_token,
            detail_table,
            json.dumps(
                [{"record_id": r["record_id"], "fields": {"适用角色": label}} for r in dev_rows],
                ensure_ascii=False,
            ),
        )
        updated = _store._parse_result(raw)
        if updated.get("ok") is not True:
            label_update_error = updated.get("message") or updated.get("error") or "适用角色 update failed"

    role_confirm = await _store.mark_done(
        bitable, app_token, detail_table, open_id=ctx["open_id"], item_id=_ROLE_CONFIRMED_ITEM, today=today
    )
    if role_confirm.get("ok") is not True:
        return json.dumps(
            {"ok": False, "error": role_confirm.get("error") or "mark_done(role_confirmed) failed"},
            ensure_ascii=False,
        )

    na_marked = 0
    revived_error = ""
    if not is_dev:
        na = await _store.mark_module_na(
            bitable,
            app_token,
            detail_table,
            open_id=ctx["open_id"],
            module=_DEV_MODULE,
            today=today,
            exclude_item_ids=frozenset({_ROLE_CONFIRMED_ITEM}),
        )
        if na.get("ok") is not True:
            return json.dumps({"ok": False, "error": na.get("error") or "mark_module_na failed"}, ensure_ascii=False)
        na_marked = na.get("marked") or 0
    else:
        # 刻意为之: 之前若选过「非研发」, 这五行已被标 不适用 —— 选回「研发」必须把它们
        # 复活成 未完成(已完成的行不动, 免得把已做完的项目倒退回未完成), 否则
        # fresh_dev_rows 会把它们继续当不适用过滤掉, 新卡发出去也是零行的死卡。
        na_rows = [r for r in dev_rows if str(r.get("状态") or "") == _p.STATUS_NA]
        if na_rows:
            revive_raw = await bitable.update_records(
                app_token,
                detail_table,
                json.dumps(
                    [{"record_id": r["record_id"], "fields": {"状态": _p.STATUS_TODO}} for r in na_rows],
                    ensure_ascii=False,
                ),
            )
            revived = _store._parse_result(revive_raw)
            if revived.get("ok") is not True:
                revived_error = revived.get("message") or revived.get("error") or "状态 revive failed"

    rows, truncated_2 = await _store.fetch_detail(bitable, app_token, detail_table, ctx["open_id"])
    truncated = truncated or truncated_2
    cfg = await _store.load_config()
    items = _cfg.load_sop(cfg)
    window = next((i.window_days for i in items if i.module == _DEV_MODULE), 7)
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), today)
    due_text = f"Day 1-{window} 截止（{_cfg.due_date(onboard, window)}）"

    fresh_dev_rows = [
        r
        for r in rows
        if str(r.get("模块") or "") == _DEV_MODULE
        and str(r.get("状态") or "") != _p.STATUS_NA
        and _store._item_id_of(r) != _ROLE_CONFIRMED_ITEM
    ]
    role_confirmed_row = next(
        (r for r in rows if _store._item_id_of(r) == _ROLE_CONFIRMED_ITEM),
        None,
    )
    card, handlers = _card.role_settled_card(
        is_dev, fresh_dev_rows, due_text, str(cfg.get("sop_doc_url") or ""), role_confirmed_row
    )
    # 刻意为之: 原地更新这张卡, 不再补发第二张。
    # 以前是 send_card 另发一张终态卡, 结果新人面前摆着两张开发环境卡(用户反馈"发了两张")。
    # 现在改成 edit + 回写 multi_use 快照 —— 光 edit_card 会让快照被判为已消费、
    # 同卡其余按钮全死, 所以两步必须成对做。
    message_id = ctx.get("message_id") or ""
    if not message_id:
        return json.dumps(
            {"ok": False, "error": "no message_id in callback; cannot update the card in place", "role": ctx["role"]},
            ensure_ascii=False,
        )
    edited = _store._parse_result(
        await feishu_message_edit_card(message_id, json.dumps(card, ensure_ascii=False), ctx.get("open_id") or "")
    )
    if edited.get("ok") is not True:
        return json.dumps(
            {
                "ok": False,
                "error": f"edit_card failed: {edited.get('message') or edited.get('error')}",
                "role": ctx["role"],
            },
            ensure_ascii=False,
        )
    # 回写 multi_use 快照: 光 edit_card 会让快照被判为已消费, 同卡其余按钮全死。
    snapshot_note = ""
    if handlers:
        try:
            rewritten = await _rewrite_card_snapshot(message_id, card, os.environ.get("PSI_APPDATA", ""))
        except Exception as exc:  # 框架不可用时不该让整次点击失败
            snapshot_note = f"snapshot rewrite unavailable: {exc!r}"
        else:
            if not rewritten:
                snapshot_note = "snapshot rewrite failed; remaining buttons may be dead"

    overview_updated = False
    overview_skipped_reason = ""
    if overview_table:
        overview = await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=ctx["open_id"],
            name=ctx["name"] or ctx["open_id"],
            role=ctx["role"],
            rows=rows,
            today=today,
        )
        overview_updated = bool(overview.get("ok"))
    else:
        overview_skipped_reason = "no overview_table_id available"

    result: dict[str, Any] = {
        "ok": True,
        "role": ctx["role"],
        "dev_items": len(fresh_dev_rows),
        "overview_updated": overview_updated,
    }
    if not is_dev:
        result["na_marked"] = na_marked
    if is_dev and not fresh_dev_rows:
        # dev_items == 0 在研发分支下是异常, 不是正常态: 新卡会是零行的死卡,
        # 不能让调用方把它当普通成功悄悄放过。
        result["ok"] = False
        result["anomaly"] = "dev role settled with zero live 开发环境 rows; card has no tick buttons"
    if revived_error:
        result["revive_error"] = revived_error
    if label_update_error:
        result["label_update_error"] = label_update_error
    if overview_skipped_reason:
        result["overview_skipped_reason"] = overview_skipped_reason
    if snapshot_note:
        result["snapshot_note"] = snapshot_note
    if truncated:
        result["truncated"] = True
    return json.dumps(result, ensure_ascii=False)
