"""Send the original-edition (v1) TODO 规范体检 report card (todo-writing-check 15:00 版本一·原版).

15:00 检测两版并行:
- 版本一·原版(本工具):「TODO 规范体检」卡 —— 结构与生产原版同款
  (马晨柯 2026-09-07 提供生产截图作规格):橙头标题「TODO 规范体检 · {period} 第{cycle}周期」、
  元信息行(生成/体检时间·范围)、点名问候、合规/待完善/缺必填三格统计、
  「📋 待完善项」逐条清单(如「小目标缺『截止日期』」)、「去修改 TODO LIST」跳转、
  底部规范依据。判定口径 = 结构必填 + 价值声明:
  大目标/小目标 必填 标题+截止(大目标另按三层对齐声明外部成果;负责人另查友商对比),
  todo 必填 标题+截止+验收人 —— 判定由调用方(定时 LLM 按 todo-writing-standard /
  todo-truthfulness-check / todo-alignment-check 技能)完成,本工具只做:
  校验入参 → 组卡 JSON → 发卡。纯信息卡(跳转按钮走链接,无 agent 回调),handlers 为空。
- 版本二·五条检测结果卡(feishu_todo_check_result_send):在原规范基础上新增五条固定检测点。

测试期:设了环境变量 ``PSI_TODO_CARD_TEST_RECEIVE_ID`` 时,所有卡改发该接收人
(验收用,不打扰真实成员);正式运行不设该变量 → 发 receive_id 本人。
测试拦截在**调用时**读环境变量(不在导入时),保证单测可动态开关。

规格:卡面为卡 2.0 JSON(column_set 统计格 + markdown),不走 DSL 渲染——
DSL 是给带交互(按钮/打分/评论)的业务卡用的,本卡是静态体检报告,
直拼 JSON 才能还原生产版式(橙头/统计三格/链接跳转);同目录
``skills/card-dsl/templates/todo-check-report.xml`` 只存档卡面规格说明。
"""

from __future__ import annotations

import json
import os

import _feishu_impl as _f

_MAX_ROWS = 40  # 待完善项逐条列全上限;超限报错不静默丢

_DEFAULT_FOOTER = "规范依据：大目标/小目标 必填 标题+截止；todo 必填 标题+截止+验收人。机器初判，以人工复核为准"
_DEFAULT_CTA_TEXT = "去修改 TODO LIST"


def _num(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stat_column(count: int, label: str) -> dict:
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [
            {
                "tag": "markdown",
                "content": f"**{count}**\n\n{label}",  # 数字与标签分行
            }
        ],
    }


def _item_line(row: dict) -> str:
    """一行待完善项:「小目标缺『截止日期』」;整句行(项)优先,否则层级+缺 拼。"""
    full = str(row.get("项") or row.get("item") or "").strip()
    if full:
        return full
    hier = str(row.get("层级") or row.get("level") or "").strip()
    missing = str(row.get("缺") or row.get("missing") or "").strip()
    if hier and missing:
        return f"{hier}缺「{missing}」"
    return hier or missing or ""


def build_report_card(
    person_name: str,
    period: str,
    cycle: str,
    meta_line: str,
    rows: list[dict],
    counts: dict,
    cta_text: str,
    cta_url: str,
    footer: str,
) -> tuple[dict, str]:
    """组「TODO 规范体检」原版卡 JSON;失败返回 (None, 错误信息)。"""
    person = (person_name or "").strip() or "该成员"
    period = (period or "").strip()
    cycle = (cycle or "").strip()
    title = "TODO 规范体检"
    if period or cycle:
        seg = period
        if cycle:
            seg = f"{seg} 第{cycle}周期" if seg else f"第{cycle}周期"
        title = f"TODO 规范体检 · {seg}"

    ok_n = _num(counts.get("合规", counts.get("ok", 0)))
    improve_n = _num(counts.get("待完善", counts.get("improve", 0)))
    must_n = _num(counts.get("缺必填", counts.get("must", 0)))

    items: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            return None, "rows_json 每项必须是对象 {层级,缺} 或 {项}"
        line = _item_line(r)
        if line:
            items.append(line)
    if len(items) > _MAX_ROWS:
        return None, f"待完善项最多 {_MAX_ROWS} 行,收到 {len(items)} 行(不该截断丢违规项)"

    elements: list[dict] = []
    meta = (meta_line or "").strip()
    if meta:
        elements.append({"tag": "markdown", "content": meta})
    elements.append(
        {"tag": "markdown", "content": f"**{person}**，机器初判你 {period} 的 TODO 填报有 规范问题，请对照修正："}
    )
    elements.append(
        {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "background_style": "grey",
            "columns": [
                _stat_column(ok_n, "合规"),
                _stat_column(improve_n, "待完善"),
                _stat_column(must_n, "缺必填"),
            ],
        }
    )
    if items:
        elements.append({"tag": "markdown", "content": "**📋 待完善项**"})
        elements.append({"tag": "markdown", "content": "\n".join(f"- {line}" for line in items)})
    cta_url = (cta_url or "").strip()
    if cta_url:
        cta_text = (cta_text or "").strip() or _DEFAULT_CTA_TEXT
        elements.append({"tag": "markdown", "content": f"[{cta_text}]({cta_url})"})
    footer = (footer or "").strip() or _DEFAULT_FOOTER
    elements.append({"tag": "markdown", "content": footer})

    return (
        {
            "schema": "2.0",
            "header": {"template": "orange", "title": {"tag": "plain_text", "content": title}},
            "body": {"elements": elements},
        },
        "",
    )


async def feishu_todo_check_report_send(
    receive_id: str = "",
    person_name: str = "",
    period: str = "",
    cycle: str = "",
    meta_line: str = "",
    rows_json: str = "[]",
    counts_json: str = "{}",
    cta_text: str = "",
    cta_url: str = "",
    footer: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send one person's original-edition (v1) TODO 规范体检 report card.

    Args:
        receive_id: The checked person's ``ou_...`` open_id (private DM).
            测试期(设了 PSI_TODO_CARD_TEST_RECEIVE_ID)会被覆盖成测试接收人。
        person_name: 被检人姓名,如 马晨柯(卡内点名)。
        period: 填报期次标签,如 ``9.2``(标题/问候里的周期)。
        cycle: 周期号,如 ``18`` → 卡题「9.2 第18周期」。
        meta_line: 元信息行整句(如「🕐 09-03 15:39 生成 · 14:50 规范体检 ·
            单发本人 · 9.2 第18周期」);留空不渲染。
        rows_json: 待完善项数组,每项 ``{"层级":"小目标","缺":"截止日期"}``
            (渲染成「小目标缺『截止日期』」)或整句 ``{"项":"…"}``;合规不传。
        counts_json: 统计三格 ``{"合规":0,"待完善":0,"缺必填":1}``。
        cta_text/cta_url: 「去修改 TODO LIST」跳转链接;url 空则不渲染 CTA。
        footer: 底部规范依据;留空用默认(不含落款,需落款调用方拼接)。
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

    card, err = build_report_card(
        person_name=person_name,
        period=period,
        cycle=cycle,
        meta_line=meta_line,
        rows=rows,
        counts=counts,
        cta_text=cta_text,
        cta_url=cta_url,
        footer=footer,
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
