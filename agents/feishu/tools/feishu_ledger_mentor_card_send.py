"""Send the 16:00 mentor ledger push card (todo-ledger-push · v2.5 入口版).

定位(马晨柯 2026-09-07 多轮定稿):主交付物是**结构化多维表格**(对比明细-<周期>:
每条 TODO 一行、关系单选,可筛可查);卡片只是**摘要入口**,让 mentor 3 秒决策
——顶部点名行(谁要处理/顺延知会)→ 全员状态表(状态固定词,不松散)→ base 链接。
本工具只做:校验行 → 从 rows 聚合点名行 → DSL 渲染(ledger-mentor-card) → 发卡。

- 状态白名单:正常 / 需处理 / 请假顺延 / 空白 / 不可达(越界整单拒发,防口径漂移)
- 点名行自动聚合:状态=需处理 → 「⚠️ {成员}:{备注}」;状态=请假顺延 → 「🏖 {成员} 顺延」
  (全员仍都在下表,一个不漏)
- 原版(16:00 纯文本 base 链接推送)保留;纯信息卡,无按钮/回传,handlers 为空
- 测试期:设了环境变量 ``PSI_TODO_CARD_TEST_RECEIVE_ID`` 时改发该测试接收人,
  调用时读 env(不在导入时),单测可动态开关。
"""

from __future__ import annotations

import json
import os

import _feishu_impl as _f
from _card_dsl import render_template

_MAX_ROWS = 40  # 全员一人一行;超过即报错,不静默截断
_STATUS_ALLOWED = ("正常", "需处理", "请假顺延", "空白", "不可达")
_DEFAULT_LEGEND = (
    "状态:正常=无遗留推进中 · 需处理=消失待确认/回流逾期请关注 · 请假顺延=假内到期不计逾期 · "
    "空白=未填 · 不可达=不在通讯录"
)


async def feishu_ledger_mentor_card_send(
    receive_id: str = "",
    mentor_name: str = "",
    last_cycle: str = "",
    cycle_date: str = "",
    group_summary: str = "",
    board_link: str = "",
    rows_json: str = "[]",
    legend: str = "",
    note: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send one mentor's ledger push card (全员状态 + 点名行 + 明细入口).

    Args:
        receive_id: The mentor's ``ou_...`` open_id (private DM).
            测试期(设了 PSI_TODO_CARD_TEST_RECEIVE_ID)会被覆盖成测试接收人。
        mentor_name: Mentor display name, e.g. 孙逊 (card title suffix).
        last_cycle / cycle_date: 上期 / 本期日期(YYYY-MM-DD)。
        group_summary: 组内摘要,如「9 人组 · 7 人已填 · 回流 1 · 消失 2」。
        board_link: 该 mentor 台账 base 链接(对比明细表所在,自动展开预览)。
        rows_json: JSON list, **全员一人一行**,每项
            ``{"成员": "黄子建", "状态": "正常", "要点": "承接 2 · 闭环 1", "备注": "A/B 实验 9.6 截止"}``。
            - 成员:必填;
            - 状态:白名单 正常/需处理/请假顺延/空白/不可达,否则整单拒发;
            - 要点/备注:可空(渲染 —)。
        legend: 图例文案,空用默认口径(与 company-todo-audit 同源)。
        note: Optional status line;空则不渲染。
        receive_id_type: Auto-detected from the id prefix; only set for a bare user_id.
        user_key: The operator's open_id (injected by Session), for auth fallback.
    """
    try:
        rows = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
    except ValueError:
        return json.dumps({"ok": False, "error": "rows_json is not valid JSON"}, ensure_ascii=False)
    if not isinstance(rows, list):
        return json.dumps({"ok": False, "error": "rows_json must be a JSON list"}, ensure_ascii=False)
    if not rows:
        return json.dumps({"ok": False, "error": "rows_json 为空(全员卡至少要有一行,不该发空卡)"}, ensure_ascii=False)
    if len(rows) > _MAX_ROWS:
        return json.dumps(
            {"ok": False, "error": f"rows_json 最多 {_MAX_ROWS} 人,收到 {len(rows)} 人(不该截断丢成员)"},
            ensure_ascii=False,
        )
    clean: list[dict[str, str]] = []
    alert_parts: list[str] = []
    leave_parts: list[str] = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            return json.dumps({"ok": False, "error": f"第 {i} 行不是对象"}, ensure_ascii=False)
        member = str(r.get("成员") or r.get("member") or "").strip()
        status = str(r.get("状态") or r.get("status") or "").strip()
        if not member:
            return json.dumps({"ok": False, "error": f"第 {i} 行缺 成员(卡按人组织,每行必须是一个人)"}, ensure_ascii=False)
        if status not in _STATUS_ALLOWED:
            return json.dumps(
                {"ok": False, "error": f"第 {i} 行 {member} 的状态「{status}」不在白名单:{'/'.join(_STATUS_ALLOWED)}"},
                ensure_ascii=False,
            )
        remark = str(r.get("备注") or r.get("remark") or "").strip()
        clean.append(
            {
                "成员": member,
                "状态": status,
                "要点": str(r.get("要点") or r.get("point") or "—").strip() or "—",
                "备注": remark or "—",
            }
        )
        if status == "需处理":
            alert_parts.append(f"{member}({remark})")
        elif status == "请假顺延":
            leave_parts.append(f"{member}({remark})")
    if alert_parts or leave_parts:
        bits = [("需处理", alert_parts), ("顺延知会", leave_parts)]
        line = " ⚠️ ".join(f"{label}:{'、'.join(parts)}" for label, parts in bits if parts)
        alert_line = f"⚠️ {line} —— 明细见下表与台账"
    else:
        alert_line = "✅ 本期无待人工处理事项(全员正常或空白),明细见下表与台账"

    values = {
        "last_cycle": (last_cycle or "").strip() or "—",
        "cycle_date": (cycle_date or "").strip() or "—",
        "mentor_name": (mentor_name or "").strip() or "—",
        "group_summary": (group_summary or "").strip() or "—",
        "board_link": (board_link or "").strip() or "—",
        "alert_line": alert_line,
        "legend": legend.strip() if legend else _DEFAULT_LEGEND,
        "note": (note or "").strip(),
    }
    rendered = render_template(
        "ledger-mentor-card",
        values_json=json.dumps(values, ensure_ascii=False),
        context_json=json.dumps({"rows": clean}, ensure_ascii=False),
    )
    if not rendered.get("ok"):
        return json.dumps(
            {"ok": False, "error": f"dsl render failed: {rendered.get('error')}"}, ensure_ascii=False
        )

    target = os.environ.get("PSI_TODO_CARD_TEST_RECEIVE_ID", "").strip() or (receive_id or "").strip()
    if not target:
        return json.dumps(
            {
                "ok": False,
                "error": "receive_id required(或设 PSI_TODO_CARD_TEST_RECEIVE_ID 走测试接收人)",
            },
            ensure_ascii=False,
        )
    result = await _f.send_card_impl(
        target,
        json.dumps(rendered["card"], ensure_ascii=False),
        receive_id_type,
        user_key or None,
        "{}",
        json.dumps(rendered["handlers"], ensure_ascii=False),
        False,
    )
    return _f.dumps_result(result)
