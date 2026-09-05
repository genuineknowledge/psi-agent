"""每日 19:00 给 HR 发一张在途新人进度日报, 带总览表链接; 顺带重算总览做兜底对账。

由 schedules/rookie-digest-daily 以 fire=prompt 触发(内容要现算聚合, fire=tool
到点不经 LLM、只能调一个工具传固定参数)。本工具自己完成聚合与发卡。
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_send_card


def active_rookies(overview_rows: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """该报给 HR 的人 = **入职第 2 天结束时仍未完成**的。

    刻意为之: 不再每天报所有在途新人。规则是「第 2 天(19:00)仍没做完才推」——
    第 1 天还在办手续, 催 HR 没有意义; 做完了的更不该出现在 HR 的待办里。
    所以这张卡不是「日报」而是「异常提醒」: 收到它就意味着有人需要人工介入。

    已出新手村的一律不报(哪怕是今天刚完成的)——完成本身不需要 HR 处理。
    """
    active: list[dict[str, Any]] = []
    for row in overview_rows:
        if str(row.get("状态") or "") == "已出新手村":
            continue
        onboard = row.get("入职日")
        if not isinstance(onboard, date):
            # 入职日缺失时宁可报出来让人看一眼, 也不要静默漏掉一个卡住的新人
            active.append(row)
            continue
        if (today - onboard).days + 1 >= 2:
            active.append(row)
    return active


async def _fetch_overview(bitable: Any, app_token: str, overview_table_id: str) -> tuple[list[dict[str, Any]], bool]:
    """拉取总览表全部行 —— 翻页直到 has_more 为假, 理由与 fetch_detail 一致:

    总览表随总在途人数增长, 迟早会超过一页; 只读第一页会让日报漏人、也会让
    兜底对账漏掉没读到的那些人。

    同样地, 只看「空 token」或「token 与上次相同」不足以防死循环: 服务端若一直
    回 has_more=true 且每次给一个新 token, 会永远翻下去。这里复用与 fetch_detail
    相同的页数上限 + 已见 token 集合。返回 (rows, truncated) —— truncated 为真
    时拿到的是不完整读取, 日报与对账都不能当成全量数据处理。
    """
    rows: list[dict[str, Any]] = []
    page_token = ""
    seen_tokens = {""}
    for _ in range(_store.MAX_PAGES):
        raw = await bitable.search_records(app_token, overview_table_id, "", page_size=500, page_token=page_token)
        rows.extend(_store._row_of(i) for i in _store._items_of(raw))
        has_more, next_token = _store._page_info(raw)
        if not has_more or not next_token or next_token in seen_tokens:
            return rows, False
        seen_tokens.add(next_token)
        page_token = next_token
    return rows, True


async def rookie_sop_digest(hr_open_id: str = "") -> str:
    """Send HR one card summarising every in-flight new hire, plus the overview table link.

    Fired by the 19:00 ``rookie-digest-daily`` schedule with ``fire=prompt`` (the schedule
    body just says "call rookie_sop_digest" — the content must be aggregated fresh across
    every in-flight new hire, which a ``fire=tool`` schedule cannot do since it calls one
    tool with fixed arguments and no LLM). This tool is therefore self-sufficient: it takes
    at most an HR open_id and does all the aggregation and sending itself.

    Before rendering, recomputes each person's overview row from their detail rows, so the
    digest also serves as the daily reconciliation pass for the overview projection. Sends
    nothing when no one is in flight; when everyone is on track it still sends
    («全部正常»), because HR must be able to tell "no news because nothing is wrong" from
    "no news because the job is broken" — it is silent only on an empty roster, never
    merely because the news is good.

    Args:
        hr_open_id: HR's Feishu open_id. Empty → ``hr_notify_id`` from
            ``config/rookie_sop.yaml``.
    """
    cfg = await _store.load_config()
    target = (hr_open_id or "").strip() or str(cfg.get("hr_notify_id") or "").strip()
    if not target:
        return json.dumps(
            {"ok": False, "error": "hr_open_id is required (or set hr_notify_id in config/rookie_sop.yaml)"},
            ensure_ascii=False,
        )

    state = await _rt.load_state()
    app_token = str(state.get("app_token") or "")
    detail_table = str(state.get("detail_table_id") or "")
    overview_table = str(state.get("overview_table_id") or "")
    if not app_token or not overview_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    today = date.today()

    overview_rows, truncated = await _fetch_overview(bitable, app_token, overview_table)

    # 兜底对账: 每人从明细整体重算一遍, 修掉任何漏写造成的漂移。recompute_overview
    # 的返回值不吞: 万一某人重算失败, 报告里要能看出是谁、不能悄悄当没发生过。
    reconcile_errors: list[str] = []
    for row in overview_rows:
        open_id = str(row.get("open_id") or "").strip()
        if not open_id or not detail_table:
            continue
        detail, detail_truncated = await _store.fetch_detail(bitable, app_token, detail_table, open_id)
        truncated = truncated or detail_truncated
        if not detail:
            continue
        role_label = next((str(r.get("适用角色") or "") for r in detail if r.get("适用角色") in {"研发", "非研发"}), "")
        role = "dev" if role_label == "研发" else "nondev" if role_label == "非研发" else ""
        recomputed = await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=open_id,
            name=str(row.get("姓名") or open_id),
            role=role,
            rows=detail,
            today=today,
        )
        if recomputed.get("ok") is not True:
            reconcile_errors.append(open_id)

    overview_rows, truncated_2 = await _fetch_overview(bitable, app_token, overview_table)
    truncated = truncated or truncated_2
    active = active_rookies(overview_rows, today)
    if not active:
        result: dict[str, Any] = {"ok": True, "sent": False, "reason": "no active rookies"}
        if reconcile_errors:
            result["reconcile_errors"] = reconcile_errors
        if truncated:
            result["truncated"] = True
        return json.dumps(result, ensure_ascii=False)

    card, handlers = _card.digest_card(
        active,
        str(state.get("table_url") or f"https://feishu.cn/base/{app_token}"),
        f"{today.month}月{today.day}日",
    )
    sent = _store._parse_result(
        await feishu_message_send_card(
            target,
            json.dumps(card, ensure_ascii=False),
            "open_id",
            "",
            json.dumps({"type": "rookie_sop_digest", "date": today.isoformat()}, ensure_ascii=False),
            json.dumps(handlers, ensure_ascii=False),
        )
    )
    # 与催办卡不同: 日报没有任何交互按钮(handlers 恒为 {}), 所以
    # callback_context_saved=false 丢的只是一份不会被用到的回调快照, 不影响
    # HR 能不能看懂这张卡 —— 不当硬失败, 只看 ok。
    if sent.get("ok") is not True:
        return json.dumps(
            {
                "ok": False,
                "error": sent.get("message") or sent.get("error") or "feishu_message_send_card failed",
            },
            ensure_ascii=False,
        )

    result = {"ok": True, "sent": True, "rookies": len(active)}
    if reconcile_errors:
        result["reconcile_errors"] = reconcile_errors
    if truncated:
        result["truncated"] = True
    return json.dumps(result, ensure_ascii=False)
