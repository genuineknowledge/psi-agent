"""Send the v2 mentor check-reminder card (mentor-check-remind 15:10 新卡).

原版(15:10 纯文本提醒 + 对齐存疑清单私聊)保留;本工具只发新卡型「mentor 检查提醒卡」:
逐人一行展示 五条判定速览(①时间 ②粒度 ③价值 ④对齐 ⑤全覆盖)+ 第四条
「个人 TODO 与小组 TODO 对齐」的依据 + 需 mentor 人工确认的存疑项。

第四条是 15:10 环节的重点:SOP「个人 TODO 要与小组 TODO 对齐」,依据 = 看板 mentor 列
当期任务的承接/拆解/支撑关系;机器初判拿不准的存疑项,留给 mentor 人工把关。
判定由调用方完成(定时 LLM 按 todo-truthfulness-check / todo-alignment-check 技能),
本工具只做:校验成员行 → DSL 渲染模板 → 发卡。纯信息卡,无按钮/回传,handlers 为空。

测试期:设了环境变量 ``PSI_TODO_CARD_TEST_RECEIVE_ID`` 时,所有卡改发该接收人
(验收用,不打扰真实 mentor);正式运行不设该变量 → 发 receive_id 本人。
测试拦截在**调用时**读环境变量(不在导入时),保证单测可动态开关。
"""

from __future__ import annotations

import json
import os

import _feishu_impl as _f
from _card_dsl import render_template

# 组内成员行上限(飞书原生 table 每页 ≤10 行,超组数可拆多卡;先硬上限防超发)。
_MAX_ROWS = 10

_BOARD_URL = "https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc"


async def feishu_mentor_check_card_send(
    receive_id: str = "",
    mentor_name: str = "",
    cycle_date: str = "",
    filled_summary: str = "",
    missing_summary: str = "",
    board_link: str = "",
    rows_json: str = "[]",
    note: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send one mentor's check-reminder card (per-member rows, alignment highlighted).

    Args:
        receive_id: The mentor's ``ou_...`` open_id (private DM).
            测试期(设了 PSI_TODO_CARD_TEST_RECEIVE_ID)会被覆盖成测试接收人。
        mentor_name / cycle_date: Header fields, e.g. mentor_name=孙逊,
            cycle_date=2026-09-04.
        filled_summary / missing_summary: e.g. ``7/9`` / ``2 人(均请假免填)``。
        board_link: The team TODO board wiki link; empty defaults to the known
            TODO LIST wiki (genuineknowledge tenant).
        rows_json: JSON list of member rows, each
            ``{"成员": "马晨柯", "判定速览": "①✅ ②⚠️ ③✅ ④❓ ⑤✅",
              "对齐依据": "承接孙逊当期…", "待确认": "⑤ 全覆盖 2 项存疑…"}``。
            判定速览建议带符号:✅ 合规 / ⚠️ 提示 / ❓ 存疑。
        note: Optional status line (e.g. 「本组有 N 条对齐存疑需人工确认」);空则不渲染。
        receive_id_type: Auto-detected from the id prefix; only set for a bare user_id.
        user_key: The operator's open_id (injected by Session), for auth fallback.
    """
    try:
        rows = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
    except ValueError:
        return json.dumps({"ok": False, "error": "rows_json is not valid JSON"}, ensure_ascii=False)
    if not isinstance(rows, list):
        return json.dumps({"ok": False, "error": "rows_json must be a JSON list"}, ensure_ascii=False)
    if len(rows) > _MAX_ROWS:
        return json.dumps(
            {
                "ok": False,
                "error": f"rows_json 最多 {_MAX_ROWS} 行(飞书原生表格一页 ≤10 行),"
                f"收到 {len(rows)} 行 —— 请按 mentor 分卡或分批",
            },
            ensure_ascii=False,
        )
    clean: list[dict[str, str]] = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            return json.dumps({"ok": False, "error": f"第 {i} 行不是对象"}, ensure_ascii=False)
        name = str(r.get("成员") or r.get("name") or "").strip()
        if not name:
            return json.dumps({"ok": False, "error": f"第 {i} 行缺 成员(必填)"}, ensure_ascii=False)
        clean.append(
            {
                "成员": name,
                "判定速览": str(r.get("判定速览") or r.get("verdicts") or "—"),
                "对齐依据": str(r.get("对齐依据") or r.get("align") or "—"),
                "待确认": str(r.get("待确认") or r.get("pending") or "—"),
            }
        )

    values = {
        "mentor_name": (mentor_name or "").strip() or "未知",
        "cycle_date": (cycle_date or "").strip() or "—",
        "filled_summary": (filled_summary or "").strip() or "—",
        "missing_summary": (missing_summary or "").strip() or "—",
        "board_link": f"[打开看板]({(board_link or _BOARD_URL).strip()})",
        "note": (note or "").strip(),
    }
    rendered = render_template(
        "mentor-check-card",
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
