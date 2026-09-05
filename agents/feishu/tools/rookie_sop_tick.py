"""勾选一条 SOP 项: 写明细完成状态, 再从明细整体重算该人的总览行。

卡片的原地重绘由框架完成, 本工具不发卡、不改卡。连点会被合并成
<feishu_card_action_batch>, 里面每条都要各调一次本工具(漏掉就丢一项完成)。
"""

from __future__ import annotations

# ruff: noqa: E402
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
import _rookie_sop_progress as _p
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_edit_card

from psi_agent.channel.feishu._card_store import rewrite_card_snapshot as _rewrite_card_snapshot

_HANDLER = "rookie_sop_tick"


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _resolve_context(payload: dict[str, Any]) -> dict[str, Any]:
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

    business = _as_dict(payload.get("business_context"))
    source = _as_dict(payload.get("source"))
    operator = str(source.get("operator_open_id") or source.get("open_id") or "").strip()

    item_id = str(value.get("item_id") or "").strip()
    if not item_id:
        action_name = str(value.get("action") or action.get("action_id") or "")
        if action_name.startswith("rookie_tick_"):
            item_id = action_name[len("rookie_tick_") :]

    return {
        "error": "",
        "open_id": str(business.get("open_id") or "").strip() or operator,
        "name": str(business.get("name") or "").strip(),
        "item_id": item_id,
        "app_token": str(business.get("app_token") or "").strip(),
        "detail_table_id": str(business.get("detail_table_id") or "").strip(),
        "overview_table_id": str(business.get("overview_table_id") or "").strip(),
        # 重绘整卡要用它: 框架只替换被点的那一块, 不会重算头部「已完成 N / M 项」
        "message_id": str(payload.get("message_id") or "").strip(),
        "module": str(business.get("module") or "").strip(),
    }


async def rookie_sop_tick(card_action_json: str = "") -> str:
    """Record one ticked onboarding SOP item, then recompute that person's overview row.

    Call this for a ``<feishu_card_action>`` whose ``dispatch.handler`` is
    ``rookie_sop_tick``. Pass the **entire** JSON object inside the tag. The card has
    already been redrawn by the framework — do not re-send it, do not narrate the click.
    Finish with zero assistant content unless this tool reports an error.

    If the payload arrived wrapped in ``<feishu_card_action_batch>``, call this once per
    ``<feishu_card_action>`` inside it (skipping one silently loses that item), then send
    at most one short summary for the whole batch.

    Args:
        card_action_json: Full ``<feishu_card_action>`` payload JSON string.
    """
    payload = _store._parse_result(card_action_json)
    if not payload:
        return json.dumps({"ok": False, "error": "card_action_json must be a JSON object"}, ensure_ascii=False)

    ctx = _resolve_context(payload)
    if ctx.get("error"):
        return json.dumps({"ok": False, "error": ctx["error"]}, ensure_ascii=False)
    if not ctx["open_id"] or not ctx["item_id"]:
        return json.dumps({"ok": False, "error": "cannot resolve open_id / item_id"}, ensure_ascii=False)

    state = await _rt.load_state()
    app_token = ctx["app_token"] or str(state.get("app_token") or "")
    detail_table = ctx["detail_table_id"] or str(state.get("detail_table_id") or "")
    overview_table = ctx["overview_table_id"] or str(state.get("overview_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    today = date.today()
    marked = await _store.mark_done(
        bitable, app_token, detail_table, open_id=ctx["open_id"], item_id=ctx["item_id"], today=today
    )
    if marked.get("ok") is not True:
        return json.dumps({"ok": False, "error": marked.get("error") or "mark_done failed"}, ensure_ascii=False)

    rows, truncated = await _store.fetch_detail(bitable, app_token, detail_table, ctx["open_id"])
    role = ""
    for row in rows:
        label = str(row.get("适用角色") or "")
        if label in {"研发", "非研发"}:
            role = "dev" if label == "研发" else "nondev"
            break

    # 刻意为之: 先重绘卡片, 再算总览。用户等的是卡面 ——
    # 总览表是给 HR 看的投影, 与这张卡的显示无关, 让它排在前面等于把两次
    # 飞书往返(查总览行 + 写总览行)算进用户的等待里。顺序换过来后, 用户感知的
    # 链路从 6 次往返降到 4 次; 总览照样会被更新, 只是发生在卡片刷新之后。
    redraw = await _redraw_card(ctx, rows, today)

    overview_updated = False
    overview_skipped_reason = ""
    if overview_table:
        overview = await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=ctx["open_id"],
            name=ctx["name"] or ctx["open_id"],
            role=role,
            rows=rows,
            today=today,
        )
        overview_updated = bool(overview.get("ok"))
    else:
        overview_skipped_reason = "no overview_table_id available"

    # 勾选后重绘整张卡。必须由本方做, 有两个原因:
    #   1) 框架只把被点的那一块换成「● ~~…~~」, 不会重算头部「已完成 N / M 项」——
    #      不重绘的话那个数字永远不动(实测踩过, 用户第一句反馈就是这个)。
    #   2) 本卡的按钮放在 div.extra 里, 而飞书的 extra 不接受 markdown, 框架那次
    #      替换会被拒(ErrCode 11310), 卡面根本不会变。
    # 重绘后必须回写 multi_use 快照: 光 edit_card 会让快照失效(变 .consumed),
    # 那样同卡其余按钮全部失灵。
    result: dict[str, Any] = {
        "ok": True,
        "item_id": ctx["item_id"],
        "already_done": bool(marked.get("already_done")),
        "overview_updated": overview_updated,
    }
    duplicates = marked.get("duplicates")
    if isinstance(duplicates, int) and duplicates > 0:
        result["duplicates"] = duplicates
    if overview_skipped_reason:
        result["overview_skipped_reason"] = overview_skipped_reason
    if truncated:
        result["truncated"] = True
    if redraw:
        result["card_redraw"] = redraw
    return json.dumps(result, ensure_ascii=False)


async def _redraw_card(ctx: dict[str, Any], rows: list[dict[str, Any]], today: date) -> str:
    """按最新明细重绘这张卡, 并保住 multi_use 快照。返回空串表示无需重绘/已跳过。

    刻意为之: 只 edit_card 是不够的 —— 它不重新注册回调, 快照会被判为已消费,
    同卡其余按钮随之全部失灵。必须同时用框架的 rewrite_card_snapshot 把新卡面
    写回快照(它专门用于"替换 multi_use 卡的内容、保留路由元数据")。
    """
    message_id = ctx.get("message_id") or ""
    module = ctx.get("module") or ""
    if not message_id or not module:
        return "no message_id/module in callback; card left as-is"

    module_rows = [r for r in rows if str(r.get("模块") or "") == module]
    if not module_rows:
        return f"no rows for module {module!r}"

    # 刻意为之: 不再 load_config + load_sop 只为拿一个 window_days ——
    # 那是一次磁盘读 yaml 加一次全清单解析(33 项), 而窗口天数可以直接从行里的
    # 截止日 - 入职日 + 1 反推出来。勾选路径每一次往返都体现为用户的等待。
    onboard = next((r["入职日"] for r in module_rows if isinstance(r.get("入职日"), date)), today)
    due = next((r["截止日"] for r in module_rows if isinstance(r.get("截止日"), date)), None)
    window = (due - onboard).days + 1 if due is not None else 1
    due_text = _rt.due_text_for(onboard, window)
    done = sum(1 for r in module_rows if str(r.get("状态") or "") == _p.STATUS_DONE)
    # sop_url 只是页脚一个链接, 为它读一次 yaml 不值得; 留空即页脚不显示链接。
    sop_url = ""

    if module == _rt.DEV_MODULE:
        role_answered = any(str(r.get("适用角色") or "") in {"研发", "非研发"} for r in module_rows)
        card, _handlers = _card.role_card(
            due_text,
            dev_rows=module_rows,
            sop_url=sop_url,
            progress_text=f"{done}/{len(module_rows)}",
            role_answered=role_answered,
            today=today,
        )
    else:
        card, _handlers = _card.module_card(
            module, module_rows, f"{done}/{len(module_rows)}", due_text, sop_url, today=today
        )

    # 传 user_key: 与 _assignment_delivery.update_progress_card 的做法一致
    # (它传 assigner_open_id), 让这次 patch 带上卡片接收者的身份。
    edited = _store._parse_result(
        await feishu_message_edit_card(message_id, json.dumps(card, ensure_ascii=False), ctx.get("open_id") or "")
    )
    if edited.get("ok") is not True:
        return f"edit_card failed: {edited.get('message') or edited.get('error')}"

    # 回写快照, 否则同卡其余按钮全死。
    try:
        rewritten = await _rewrite_card_snapshot(message_id, card, os.environ.get("PSI_APPDATA", ""))
    except Exception as exc:  # 框架不可用时不该让整次勾选失败
        return f"redrawn but snapshot rewrite unavailable: {exc!r}"
    if not rewritten:
        return "redrawn but snapshot rewrite failed; remaining buttons may be dead"
    return ""
