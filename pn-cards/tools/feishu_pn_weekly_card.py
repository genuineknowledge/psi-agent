# ruff: noqa: RUF002  # 中文全角标点是刻意排版
"""场景④ 周小结卡（本周记录汇总 / 查看 / 复盘）。

给某人在**本周（周一~今天）**真台账里已登记的行为记录出一张小结卡：条数 +
状态分布（待复核/已通过/已调整/已退回）+ 逐条列表。卡面两动作（用户定稿）：
查看本周记录 / 开始复盘。

- 查看本周记录 —— 把每条记录展开成全文（含备注/日期），按钮撤掉只剩「开始复盘」。
- 开始复盘 —— 对本周年小结开启复盘对话：工具只负责留痕 + 刷终态（卡片原地更新
  为「已开始本周复盘」），复盘对话由 agent 在后续轮次驱动。

数据纪律：周小结从真台账取数（对象==本人 且 会议日期文本里的 MM-DD 落在本周），
台账没有极性/分数列，就只展示条数/状态分布/逐条 —— 不编造台账里没有的维度。
台账读取失败 → 不出卡、如实报错（宁可没有小结，也不发一张空卡假装有数）。

工具一卡两用：发卡（传 receive_id + ledger 坐标）/ 处理点击（传 card_action_json，
action 为 pn_week_view / pn_week_review）。开始复盘后整卡不再响应任何按钮。
"""

from __future__ import annotations

import json
from typing import Any

import _feishu_impl as _core


def _fresh_core():
    """每次(重)载本文件时按磁盘路径现取 _pn_impl，绕开下划线共享模块的进程内
    import 缓存（工具框架只按文件 hash 热载顶层工具，_ 前缀 core 不会刷新）。
    """
    import importlib.util as _ilu  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    _src = _Path(__file__).resolve().parent / "_pn_impl.py"
    _spec = _ilu.spec_from_file_location("_pn_impl_fresh", _src)
    _m = _ilu.module_from_spec(_spec)
    if _spec.loader is None:
        raise RuntimeError(f"cannot load {_src}")
    _spec.loader.exec_module(_m)
    return _m


P = _fresh_core()


async def _rebuild_weekly_card(weekly: dict, message_id: str, user_key: str) -> dict[str, Any]:
    try:
        res = await _core.edit_card_impl(
            message_id, json.dumps(P.render_weekly_card(weekly), ensure_ascii=False), user_key
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res}
    return {"ok": True}


async def _handle_click(card_action_json: str, user_key: str = "") -> str:
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    if action not in (P._ACTION_WEEK_VIEW, P._ACTION_WEEK_REVIEW):
        return json.dumps({"ok": False, "error": "unrecognized weekly action"}, ensure_ascii=False)
    weekly_id = str(act.get("weekly_id") or "").strip()
    if not weekly_id:
        return json.dumps({"ok": False, "error": "no weekly_id in value"}, ensure_ascii=False)
    weekly = await P.load_weekly(weekly_id)
    if not weekly:
        return json.dumps({"ok": False, "error": f"weekly {weekly_id} not found"}, ensure_ascii=False)
    if weekly["decided"] in P._FINAL_WEEKLY:
        return json.dumps({"ok": True, "unchanged": True, "decided": weekly["decided"]}, ensure_ascii=False)
    op = act.get("_operator") or user_key or ""
    out: dict[str, Any] = {"ok": True, "action": action}
    if action == P._ACTION_WEEK_VIEW:
        weekly["under_view"] = True
        await P.save_weekly(weekly)
        out["view_expanded"] = True
    else:  # 开始本周复盘
        weekly["decided"] = "reviewed"
        weekly["decided_by"] = op
        weekly["decided_at"] = P._now()
        await P.save_weekly(weekly)
        out["review_started"] = True
    target = act.get("_message_id") or str(weekly.get("message_id") or "")
    rebuilt: dict[str, Any] = {"ok": True, "skipped": "no card to rebuild"}
    if target:
        rebuilt = await _rebuild_weekly_card(weekly, target, op)
    if rebuilt.get("ok") is False:
        out["warn"] = f"card rebuild failed: {rebuilt.get('error')}"
    return json.dumps(out, ensure_ascii=False, default=str)


async def feishu_pn_weekly_card(
    receive_id: str = "",
    person_name: str = "",
    ledger_app_token: str = "",
    ledger_table_id: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
    card_action_json: str = "",
) -> str:
    """正负面「判断卡」场景④——周小结（发卡 / 处理点击两用）。

    **发卡**：从真台账拉某人本周（周一~今天）已登记的行为记录并汇总成卡。
    ledger_app_token/table_id **必填**（周小结的数据源就是台账，没有台账就没有
    小结）；按「对象」列匹配本人、按「会议日期」文本里的 MM-DD 判断是否本周。
    台账读取失败 → 不出卡、如实报错。

    **处理点击**（传 card_action_json）：action 为 pn_week_view（查看本周记录，
    展开全文明细，按钮只剩开始复盘）/ pn_week_review（开始本周复盘，整卡刷成
    已开始本周复盘）。开始复盘后不再响应，整卡原地更新。

    Args:
        receive_id: 本人 open_id（发卡时必填）。
        person_name: 本人姓名（卡面称呼 + 台账「对象」列匹配）。
        ledger_app_token: 真实台账 base app_token（必填）。
        ledger_table_id: 真实台账 table_id（必填）。
        receive_id_type: 收件人 id 类型（默认 open_id）。
        user_key: 调用者/点击者 open_id。
        card_action_json: 点击回调 payload（处理点击时传）。

    Returns:
        JSON：ok / action(sent|pn_week_view|pn_week_review) / weekly_id / message_id
        / counts（本周条数与状态分布）/ view_expanded / review_started / warn / error。
    """
    if card_action_json.strip():
        return await _handle_click(card_action_json, user_key)
    if not receive_id.strip():
        return json.dumps({"ok": False, "error": "receive_id is required"}, ensure_ascii=False)
    app_token = ledger_app_token.strip()
    table_id = ledger_table_id.strip()
    if not (app_token and table_id):
        return json.dumps(
            {
                "ok": False,
                "error": ("ledger_app_token and ledger_table_id are required (weekly summary reads the real ledger)"),
            },
            ensure_ascii=False,
        )
    name = (person_name or "").strip()
    if not name:
        return json.dumps(
            {"ok": False, "error": "person_name is required (matches the ledger 对象 column)"},
            ensure_ascii=False,
        )
    rows, week_label = await P.fetch_person_week_records(app_token, table_id, name, user_key)
    if rows is None:
        return json.dumps({"ok": False, "error": f"ledger read failed: {week_label}"}, ensure_ascii=False)
    weekly = P.build_weekly(
        person_open_id=receive_id.strip(),
        person_name=name,
        week_label=week_label,
        records=rows,
        ledger={"app_token": app_token, "table_id": table_id},
    )
    await P.save_weekly(weekly)
    handlers = P.weekly_card_handlers(weekly)
    if not handlers:
        return json.dumps({"ok": False, "error": "nothing to act on (weekly already final)"}, ensure_ascii=False)
    try:
        res = await _core.send_card_impl(
            receive_id=receive_id.strip(),
            card_json=json.dumps(P.render_weekly_card(weekly), ensure_ascii=False),
            receive_id_type=receive_id_type.strip() or "open_id",
            user_key=user_key,
            business_context_json=json.dumps(
                {"kind": "pn_weekly", "weekly_id": weekly["weekly_id"], "person": name},
                ensure_ascii=False,
            ),
            action_handlers_json=json.dumps(handlers, ensure_ascii=False),
            multi_use=True,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{e!r}"}, ensure_ascii=False, default=str)
    if not res.get("ok"):
        return json.dumps(
            {"ok": False, "error": res.get("message") or res.get("error") or res}, ensure_ascii=False, default=str
        )
    weekly["message_id"] = res.get("message_id", "")
    await P.save_weekly(weekly)
    counts = dict.fromkeys(P._WEEK_STATUSES, 0)
    for r in rows:
        s = str(r.get("status") or "").strip()
        counts[s] = counts.get(s, 0) + 1
    return json.dumps(
        {
            "ok": True,
            "action": "sent",
            "weekly_id": weekly["weekly_id"],
            "message_id": res.get("message_id", ""),
            "week_label": week_label,
            "counts": {"total": len(rows), "statuses": {k: v for k, v in counts.items() if v}},
            "person": name,
        },
        ensure_ascii=False,
        default=str,
    )
