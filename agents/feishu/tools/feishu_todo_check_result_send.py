"""Send the v4 generic TODO check-result card (todo-writing-check 15:00 通用卡).

v4(2026-09-08 马晨柯):需求固化进工具逻辑——
  ① 表格三列:第一列「检测项」(单元格内容 = 序号+检测项,序号自动生成)、
     第二列「结果」、第三列「说明」;
  ② 统计三格:✅ 合规 N 项 / ⚠️ 待完善 N 项 / ❌ 缺必填 N 项(灰底 column_set);
  ③ 卡片带「去修改 TODO LIST」进入链接按钮(cta_url,默认指向团队 TODO 看板)。
不再走 DSL 渲染(DSL 表达不了 column_set 统计格,与版本一原版同理由):本工具
直接组卡 2.0 JSON,校验入参 → 组卡 → 发卡。纯信息卡(跳转按钮走链接,无 agent
回调),handlers 为空。

检测项为通用逻辑:不区分「五条」与「原规范」,任意检测项一次列全;判定值建议带
符号:✅ 合规 / ⚠️ 提示 / ❓ 存疑 / ❌ 违规 / ❌ 缺必填。判定由调用方完成
(定时 LLM 按 todo-truthfulness-check / todo-alignment-check / todo-writing-standard
技能逐项判定),本工具只做校验与呈现。

测试期:设了环境变量 ``PSI_TODO_CARD_TEST_RECEIVE_ID`` 时,所有卡改发该接收人
(验收用,不打扰真实成员);正式运行不设该变量 → 发 receive_id 本人。
测试拦截在**调用时**读环境变量(不在导入时),保证单测可动态开关。
"""

from __future__ import annotations

import json
import os

import _feishu_impl as _f

_MAX_ROWS = 40  # 检测项上限(五条 + 原规范必填项全列也够);超过即报错,不截断静默丢检测点

_DEFAULT_CTA_TEXT = "去修改 TODO LIST"
# 团队 TODO 看板(genuineknowledge wiki):调用方不传 cta_url 时默认指向这里,
# 保证每次发卡都带进入链接(需求固化)。
_DEFAULT_CTA_URL = "https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc"

_INTRO = "机器初判，仅供自查参考；有异议找 mentor 复核或按 SOP 申诉。本次检测覆盖目标规范与执行质量，全部检测项一次完成。"


def _num(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stat_column(count: int, label: str) -> dict:
    """统计三格之一:label 行 + 数字行(如「**✅ 合规** / 3 项」)。"""
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [
            {
                "tag": "markdown",
                "content": f"**{label}**\n{count} 项",
            }
        ],
    }


def build_check_card(
    person_name: str,
    cycle_date: str,
    rows: list[dict],
    counts: dict,
    cta_text: str,
    cta_url: str,
) -> tuple[dict | None, str]:
    """组「TODO 检测」通用卡 JSON;失败返回 (None, 错误信息)。"""
    person = (person_name or "").strip() or "该成员"
    cycle = (cycle_date or "").strip()
    title = "TODO 检测"
    if cycle:
        title = f"TODO 检测 · {cycle}"

    ok_n = _num(counts.get("合规", counts.get("ok", 0)))
    improve_n = _num(counts.get("待完善", counts.get("improve", 0)))
    must_n = _num(counts.get("缺必填", counts.get("must", 0)))

    # 校验行数据 + 自动编号(第一列 = 序号+检测项)
    clean_rows: list[dict[str, str]] = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            return None, f"第 {i} 行不是对象"
        point = str(r.get("检测项") or r.get("检测点") or r.get("point") or "").strip()
        verdict = str(r.get("结果") or r.get("判定") or r.get("verdict") or "").strip()
        detail = str(r.get("说明") or r.get("detail") or "").strip()
        if not (point and verdict):
            return None, f"第 {i} 行缺 检测项 或 结果(每条必填;说明可空)"
        clean_rows.append({"c0": f"{i}. {point}", "c1": verdict, "c2": detail or "—"})
    if len(clean_rows) > _MAX_ROWS:
        return None, f"检测项最多 {_MAX_ROWS} 行,收到 {len(clean_rows)} 行(不该截断丢检测点)"

    elements: list[dict] = [
        {"tag": "markdown", "content": f"**被检人：** {person}"},
        {"tag": "markdown", "content": _INTRO},
        {"tag": "hr"},
        {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "background_style": "grey",
            "columns": [
                _stat_column(ok_n, "✅ 合规"),
                _stat_column(improve_n, "⚠️ 待完善"),
                _stat_column(must_n, "❌ 缺必填"),
            ],
        },
        {"tag": "hr"},
        {
            "tag": "table",
            "columns": [
                {"name": "c0", "display_name": "检测项", "data_type": "text", "width": "auto"},
                {"name": "c1", "display_name": "结果", "data_type": "text", "width": "auto"},
                {"name": "c2", "display_name": "说明", "data_type": "text", "width": "auto"},
            ],
            "rows": clean_rows,
            "page_size": max(1, min(len(clean_rows), 10)),
            "row_height": "low",
            "header_style": {"background_style": "grey", "bold": True},
        },
    ]

    cta_url = (cta_url or "").strip() or _DEFAULT_CTA_URL
    cta_text = (cta_text or "").strip() or _DEFAULT_CTA_TEXT
    elements.append(
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": cta_text},
            "type": "primary",
            "action_type": "link",
            "url": cta_url,
            "width": "fill",
        }
    )

    return (
        {
            "schema": "2.0",
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
            "body": {"elements": elements},
        },
        "",
    )


async def feishu_todo_check_result_send(
    receive_id: str = "",
    person_name: str = "",
    cycle_date: str = "",
    rows_json: str = "[]",
    counts_json: str = "{}",
    cta_text: str = "",
    cta_url: str = "",
    note: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send one person's generic TODO check-result card.

    Args:
        receive_id: The checked person's ``ou_...`` open_id (private DM).
            测试期(设了 PSI_TODO_CARD_TEST_RECEIVE_ID)会被覆盖成测试接收人。
        person_name / cycle_date: Header fields, e.g. person_name=马晨柯,
            cycle_date=2026-09-07.
        rows_json: JSON list of result rows, each
            ``{"检测项": "无过去时间点", "结果": "✅ 合规", "说明": "大目标12月…"}``。
            兼容旧字段名:检测点→检测项,判定→结果;说明可空(留 "—")。
            序号由本工具自动生成(1. / 2. / …),调用方无需传。
        counts_json: 统计三格 ``{"合规":3,"待完善":4,"缺必填":3}``(兼容 ok/improve/must)。
        cta_text / cta_url: 「去修改 TODO LIST」跳转按钮;url 空则用默认看板链接。
        note: Optional status line (e.g. 「8.17-8.27 事假,当期免填」);空则不渲染。
        receive_id_type: Auto-detected from the id prefix; only set for a bare user_id.
        user_key: The operator's open_id (injected by Session), for auth fallback.
    """
    try:
        rows = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
    except ValueError:
        return json.dumps({"ok": False, "error": "rows_json is not valid JSON"}, ensure_ascii=False)
    if not isinstance(rows, list):
        return json.dumps({"ok": False, "error": "rows_json must be a JSON list"}, ensure_ascii=False)
    try:
        counts = json.loads(counts_json) if isinstance(counts_json, str) else counts_json
    except ValueError:
        return json.dumps({"ok": False, "error": "counts_json is not valid JSON"}, ensure_ascii=False)
    if not isinstance(counts, dict):
        return json.dumps({"ok": False, "error": "counts_json must be a JSON object"}, ensure_ascii=False)

    card, err = build_check_card(
        person_name=person_name,
        cycle_date=cycle_date,
        rows=rows,
        counts=counts,
        cta_text=cta_text,
        cta_url=cta_url,
    )
    if err or card is None:
        return json.dumps({"ok": False, "error": err or "组卡失败"}, ensure_ascii=False)

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
        json.dumps(card, ensure_ascii=False),
        receive_id_type,
        user_key or None,
        "{}",
        "{}",
        False,
    )
    return _f.dumps_result(result)
