"""Card DSL implementation: parse + validate + compile the generic card language.

This module is the first build of the **通用卡片 DSL 渲染引擎** (card DSL &
rendering engine, experiment one). Business code declares a card in a small XML
DSL (elements: card/info/score/comment/action-row/button), this module compiles
the declaration into Feishu card 2.0 JSON — absorbing the Feishu protocol
restrictions (button single-use → round naming; input value → confirm; unsupported
components → simply not in the vocabulary) so business XML never mentions a Feishu
concept.

Layering (per the design doc):

    XML DSL (business declaration, human/LLM written)
      ↓ this module
    Feishu card 2.0 JSON (platform protocol, machine read)
      ↓ Feishu client
    the card the user sees

Three-tier element model:

    ① element — what it is      (<button .../>)
    ② attribute — how/what semantics (type="reject" = 驳回 semantics)
    ③ mapping — semantics → Feishu styles (engine table: reject → danger/red)

Colors never appear in the XML: business writes semantics, the mapping table
owns colors, so a design-system swap touches one table and no XML.

Vocabulary (first version, open brick-box — assemble per scene, grow on demand):

    card        title / template          root container (title + header color)
    info        label / value             display line (owner, deadline, ...)
    score       min / max / rounds /
                bind-record / selected    score button group (multi-round re-pick)
    comment     placeholder / bind-record comment input (multi-edit)
    action-row  —                         layout row for buttons
    button      text / type / action      action button (accept/reject/...)

Semantic color mapping (fixed rules, not free styling):

    button type → Feishu button.type: accept→primary(blue; green is design intent
    but Feishu has no green button), reject/danger→danger(red), default→default,
    primary→primary.
    card template → Feishu header.template: blue/green/red/grey (native support).

Action lifecycle (the six rings, spec 3.2.2): every interactive element declares
an ``action``; the engine emits round-named actions (``{action}_r{round}``),
pre-registers handlers for all rounds, assembles the callback value from
``bind-record`` + caller context + action + score + round, and rebuilds are done
by re-rendering with round+1. Business actions (ledger writes etc.) stay in the
existing direct-dispatch tools — this engine only compiles cards.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

from _runtime_paths import agent_dir
from _todo_card_impl import (
    _UNDO_ROUNDS,
    _build_card_from_state,
    _tick_action_id,
    _untick_action_id,
)

# ── Vocabulary constants ──────────────────────────────────────────────────────

# card template → Feishu header template (native colors, full mapping).
_TEMPLATE_COLORS = {
    "blue": "blue",
    "green": "green",
    "red": "red",
    "grey": "grey",
}

# button type → Feishu button.type (semantic color mapping).
# 飞书按钮只有 default/primary/danger 三种颜色:accept 的"绿色"是设计意图,
# 先落到 primary(蓝);飞书开放绿色后改这一行即可,DSL 与业务 XML 不动。
_BUTTON_TYPES = {
    "accept": "primary",
    "reject": "danger",
    "danger": "danger",
    "default": "default",
    "primary": "primary",
}

# score/comment 默认动作名,业务可在元素上显式覆盖(action 属性)。
_DEFAULT_SCORE_ACTION = "review_score"
_DEFAULT_COMMENT_ACTION = "review_input"

# 引擎内置的动作 → 直调工具映射(评价卡验证过的三个动作)。
_BUILTIN_HANDLERS = {
    _DEFAULT_SCORE_ACTION: "feishu_review_card_select",
    _DEFAULT_COMMENT_ACTION: "feishu_review_input",
    "review_reject": "feishu_review_reject",
    # 文档示例用 `action="reject"` 表示「打回重做」——reject 是按钮 type 同名的
    # 语义动作,补成内置别名,文档示例无需 handler_overrides 即可原样编译。
    "reject": "feishu_review_reject",
}

# 轮次上限:飞书 action 单卡单次消费,每重建一次轮次 +1 生成全新 action,
# 卡片才能反复操作;预注册 _MAX_ROUNDS 轮(与评价卡实卡验证一致)。
# 模板可在 <score rounds="N"> 上声明更短的轮次(见 _parse_rounds)。
# 单一真源 = _todo_card_impl._UNDO_ROUNDS:三模块共用同一上限,任一处漂移都会
# 让预注册深度与重建轮次错位(某轮变死键),故不再各写字面量 20,统一取此常量。
_MAX_ROUNDS = _UNDO_ROUNDS


def _parse_round(raw: Any) -> int:
    """Parse an int-ish value into a bounded round index (0.._MAX_ROUNDS-1).

    Used for ``selected``(选中分数,非轮次,钳到合法区间即可)与历史轮次值;
    ``render_card`` 的 round_ 参数不从这里走——它需要区分「轮次用尽
    (>= _MAX_ROUNDS)」与「非法输入(回 0)」,在调用侧单独解析。
    """
    try:
        return max(0, min(int(raw), _MAX_ROUNDS - 1))
    except (TypeError, ValueError):
        return 0


def _parse_rounds(raw: Any) -> int:
    """Parse the ``<score rounds="N">`` attribute — handler pre-registration depth.

    XSD declares ``rounds`` as positiveInteger (default 20). 0 / negative /
    non-numeric is invalid per XSD, so it falls back to the engine default —
    over-registering a handler can never break a click, under-registering can
    (round > depth → no handler → dead button). The cap keeps pre-registration
    bounded (more rounds than the engine supports is clamped, not honoured).
    """
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _MAX_ROUNDS
    if n < 1:
        return _MAX_ROUNDS
    return min(n, _MAX_ROUNDS)


# ── Validation ────────────────────────────────────────────────────────────────


def _validate(card_xml: str) -> tuple[ET.Element | None, str]:
    """Parse and validate a DSL declaration against the first-version vocabulary.

    Returns ``(root, "")`` on success, or ``(None, error)``. Validation is a
    hand-rolled equivalent of the XSD shipped beside the skill doc — the XSD is
    the human/LLM-readable spec, this keeps runtime checks dependency-free.
    """
    if not isinstance(card_xml, str) or not card_xml.strip():
        return None, "card_xml is required (the card DSL XML declaration)"
    try:
        root = ET.fromstring(card_xml)
    except ET.ParseError as e:
        return None, f"card_xml is not valid XML: {e}"
    if root.tag != "card":
        return None, f"root element must be <card>, got <{root.tag}>"
    title = (root.get("title") or "").strip()
    if not title:
        return None, "<card> requires a title attribute"
    template = (root.get("template") or "blue").strip()
    if template not in _TEMPLATE_COLORS:
        return None, f"<card template={template!r}> unknown — use blue/green/red/grey"
    return root, ""


# ── Compilation ───────────────────────────────────────────────────────────────


def _markdown_line(label: str, value: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": f"**{label}**：{value}"}  # noqa: RUF001 (卡片文案使用全角标点)


def _table_blocks(element: ET.Element, context: dict[str, Any]) -> list[dict[str, Any]]:
    """把预取的 Bitable 行渲染成卡片块(引擎不做 I/O,行数据来自 context)。

    ``<table source="rows" empty="暂无数据"><col field="标题" label="任务"/>...</table>``
    - ``source``:context 里的键,值应是 [{fields:{...}} 或 {...}] 的行数组
      (search_bitable_records_impl 返回的 records 形状:每项 {record_id, fields})。
    - ``<col field label>``:取每行 fields[field] 显示成 "**label**:值",
      未声明 col 时显示该行所有字段。
    - 行与行之间插 hr 分隔;空数据显示 ``empty`` 文案。
    行数据是调用方预取注入的,故引擎保持纯函数、可离线测。
    """
    source = (element.get("source") or "rows").strip()
    empty_text = (element.get("empty") or "暂无数据").strip()
    cols: list[tuple[str, str]] = []
    for col in element:
        if col.tag != "col":
            raise ValueError(f"<table> only holds <col>, got <{col.tag}>")
        field = (col.get("field") or "").strip()
        if not field:
            raise ValueError("<col> requires a field attribute")
        cols.append((field, (col.get("label") or field).strip()))
    raw = context.get(source)
    rows = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    if not rows:
        return [_markdown_line("提示", empty_text)]
    # 用飞书 2.0 原生 table 组件渲染成真表格(而非 markdown 文字块)。
    # 未声明 col 时取首行全部字段作列。列 name 用 c0/c1… 内部名,display_name 显示标签。
    first = rows[0].get("fields") if isinstance(rows[0].get("fields"), dict) else rows[0]
    pairs = cols or [(k, k) for k in first]
    columns = [
        {"name": f"c{i}", "display_name": label, "data_type": "text", "width": "auto"}
        for i, (_field, label) in enumerate(pairs)
    ]
    table_rows: list[dict[str, Any]] = []
    for row in rows:
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else row
        table_rows.append({f"c{i}": _cell_text(fields.get(field)) for i, (field, _label) in enumerate(pairs)})
    return [
        {
            "tag": "table",
            "page_size": max(1, min(len(table_rows), 10)),
            "row_height": "low",
            "header_style": {"background_style": "grey", "bold": True},
            "columns": columns,
            "rows": table_rows,
        }
    ]


def _cell_text(val: Any) -> str:
    """把 Bitable 单元格值转成展示文本(字段类型多样:文本/数组/对象)。"""
    if val is None:
        return "—"
    if isinstance(val, list):
        # 多选/人员/链接等数组字段:取每项的 text/name,回退 str。
        parts = [str(x.get("text") or x.get("name") or x) if isinstance(x, dict) else str(x) for x in val]
        return "、".join(p for p in parts if p) or "—"
    if isinstance(val, dict):
        return str(val.get("text") or val.get("name") or val)
    return str(val)


def _base_value(element: ET.Element, action: str, score: int, round_: int, context: dict[str, Any]) -> dict[str, Any]:
    """Assemble the callback value shared by one interactive element.

    = spec 3.2.2 ring 4 (回调组装):``bind-record`` + caller context + action +
    score + round are merged into the value. ``bind-record`` wins over a context
    ``record_id``; context supplies the rest (owner/title/cycle/task_guid/...).
    """
    value: dict[str, Any] = dict(context)
    # 安全:action/round/action_id/score/bind_field 是引擎自管字段,不容 context 注入
    # (否则恶意 context 可伪造分数、伪造回写字段、甚至试图篡改动作名破坏墓碑去重)。
    # 先从 context 副本里剔除这些保留键,再由引擎按元素声明重新赋权威值。
    for _reserved in ("action", "round", "action_id", "score", "bind_field"):
        value.pop(_reserved, None)
    bind_record = (element.get("bind-record") or "").strip()
    if bind_record:
        value["record_id"] = bind_record
    # bind-field:声明要写回的台账字段名,回调侧据此把选中值写进该列(通用回写,
    # 见 write_back_from_callback)。不声明则元素只回调不落库(保持既有行为)。
    bind_field = (element.get("bind-field") or "").strip()
    if bind_field:
        value["bind_field"] = bind_field
    value["action"] = action
    value["round"] = round_
    if score:
        value["score"] = score
    return value


def _score_columns(
    element: ET.Element, round_: int, context: dict[str, Any], occurrence: int = 0
) -> list[dict[str, Any]]:
    min_raw = element.get("min") or "1"
    max_raw = element.get("max") or "5"
    try:
        lo, hi = int(min_raw), int(max_raw)
    except ValueError:
        lo, hi = 1, 5
    lo, hi = max(1, lo), min(9, hi)
    if hi < lo:
        hi = lo
    selected = _parse_round(element.get("selected") or 0)
    action = (element.get("action") or _DEFAULT_SCORE_ACTION).strip()
    columns: list[dict[str, Any]] = []
    for score in range(lo, hi + 1):
        is_selected = score == selected
        # 同一 action 的第二个 score 组起,把出现序号折进 action_id(与按钮/评论框
        # 同规则):两个评分组若同 action 同 min/max,action_id 会完全撞车,后一组
        # 的每次点击都被前一组已消费的同名 action 墓碑拦截——出现序号保证唯一。
        action_id = f"{action}_{score}_r{round_}" if occurrence == 0 else f"{action}_{score}_{occurrence}_r{round_}"
        value = _base_value(element, f"{action}_r{round_}", score, round_, context)
        value["action_id"] = action_id
        columns.append(
            {
                "tag": "column",
                "width": "auto",
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"✓ {score}分" if is_selected else f"{score}分"},
                        "type": "primary" if is_selected else "default",
                        "behaviors": [{"type": "callback", "value": value}],
                    }
                ],
            }
        )
    return columns


def _comment_input(
    element: ET.Element, round_: int, context: dict[str, Any], index: int = 0, occurrence: int = 0
) -> dict[str, Any]:
    action = (element.get("action") or _DEFAULT_COMMENT_ACTION).strip()
    placeholder = (element.get("placeholder") or "写点评语（可选）").strip()  # noqa: RUF001 (卡片文案使用全角标点)
    # confirm 弹窗文案默认按「评价卡评语」措辞;审批卡/其它卡型可用 confirm-title /
    # confirm-text 覆盖,不再复用评价卡文案(见 T-113 观察:文案与卡能力对不上)。
    confirm_title = (element.get("confirm-title") or "确认评语").strip()
    confirm_text = (element.get("confirm-text") or "把这条评语写入台账？").strip()  # noqa: RUF001 (卡片文案使用全角标点)
    comment_value = str(context.get("comment_value") or "")
    value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
    # 同一 action 的第二个 comment 起,把出现序号折进 action_id,回调侧才能区分
    # 评语来自哪个框(name 已按元素序号唯一,飞书按 name 收值数据不丢,这里补标识)。
    value["action_id"] = f"{action}_r{round_}" if occurrence == 0 else f"{action}_{occurrence}_r{round_}"
    record_id = str(value.get("record_id") or "r")
    input_el: dict[str, Any] = {
        # confirm 字段让输入框带出「确认」按钮:点确认才把输入文字带回回调
        # (不带 confirm 的 input 输入后没有任何事件,值收不到)。
        "tag": "input",
        "input_type": "text",
        # index 掺入 name:飞书按 input name 收值,同卡多个 comment 若共用
        # bind-record 会撞名互相覆盖,加元素序号保证唯一。
        "name": f"comment_{record_id}_{index}",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        # value 是 input 的初始文本:重建时把上次评语带回输入框,可继续编辑。
        "value": comment_value,
        "confirm": {
            "title": {"tag": "plain_text", "content": confirm_title},
            "text": {"tag": "plain_text", "content": confirm_text},
        },
        "behaviors": [{"type": "callback", "value": value}],
    }
    return input_el


def _button_element(element: ET.Element, round_: int, context: dict[str, Any], occurrence: int = 0) -> dict[str, Any]:
    text = (element.get("text") or "").strip()
    if not text:
        text = "按钮"
    type_raw = (element.get("type") or "default").strip()
    feishu_type = _BUTTON_TYPES.get(type_raw)
    if feishu_type is None:
        raise ValueError(f"<button type={type_raw!r}> unknown — use accept/reject/danger/default/primary")
    action = (element.get("action") or "").strip()
    if not action:
        raise ValueError("<button> requires an action attribute")
    # 同一 action 的按钮重复出现时,把出现序号折进 action_id,回调侧才能区分
    # 点的是哪个;首次出现保持原名(与既有行为/测试一致)。handler 仍按 action 分发。
    action_id = f"{action}_r{round_}" if occurrence == 0 else f"{action}_{occurrence}_r{round_}"
    value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
    value["action_id"] = action_id
    btn: dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": feishu_type,
        "behaviors": [{"type": "callback", "value": value}],
    }
    # confirm:危险操作(打回/删除)点击弹二次确认,飞书 button 原生 confirm 字段。
    # 声明 confirm 属性即启用,弹窗正文取该属性;标题可选 confirm-title,缺省"确认操作"。
    confirm_text = (element.get("confirm") or "").strip()
    if confirm_text:
        confirm_title = (element.get("confirm-title") or "确认操作").strip()
        btn["confirm"] = {
            "title": {"tag": "plain_text", "content": confirm_title},
            "text": {"tag": "plain_text", "content": confirm_text},
        }
    return btn


def _date_picker(element: ET.Element, round_: int, context: dict[str, Any], occurrence: int = 0) -> dict[str, Any]:
    """Compile <date> into a Feishu 2.0 date_picker (点选即回调)。

    回调契约(实卡实测 2026-08-28):选中日期在回调**顶层 action.option**
    (形如 "2026-09-02 +0800",另带 action.timezone),不在 value 里——handler
    取日期读 payload["action"]["option"],value 只承载 action/round/record_id。
    """
    action = (element.get("action") or "").strip()
    placeholder = (element.get("placeholder") or "选择日期").strip()
    value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
    value["action_id"] = f"{action}_r{round_}" if occurrence == 0 else f"{action}_{occurrence}_r{round_}"
    picker: dict[str, Any] = {
        "tag": "date_picker",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "behaviors": [{"type": "callback", "value": value}],
    }
    initial = (element.get("initial-date") or "").strip()
    if initial:
        picker["initial_date"] = initial
    return picker


def _select_static(element: ET.Element, round_: int, context: dict[str, Any], occurrence: int = 0) -> dict[str, Any]:
    """Compile <select> + <option> children into a Feishu 2.0 select_static。

    回调契约(实卡实测 2026-08-28):选中项的 value 在回调**顶层 action.option**
    (即被选 <option> 的 value 属性),不在 behaviors.value 里——handler 取选项读
    payload["action"]["option"],value 只承载 action/round/record_id。
    """
    action = (element.get("action") or "").strip()
    placeholder = (element.get("placeholder") or "请选择").strip()
    options: list[dict[str, Any]] = []
    for opt in element:
        if opt.tag != "option":
            raise ValueError(f"<select> only holds <option>, got <{opt.tag}>")
        text = (opt.get("text") or "").strip()
        opt_value = (opt.get("value") or "").strip()
        if not text or not opt_value:
            raise ValueError("<option> requires text and value attributes")
        options.append({"text": {"tag": "plain_text", "content": text}, "value": opt_value})
    if not options:
        raise ValueError("<select> requires at least one <option>")
    value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
    value["action_id"] = f"{action}_r{round_}" if occurrence == 0 else f"{action}_{occurrence}_r{round_}"
    return {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "options": options,
        "behaviors": [{"type": "callback", "value": value}],
    }


def _resolve_handler(action: str, extra_handlers: dict[str, str]) -> str:
    """Look an action up in builtins then the caller's overrides."""
    handler = _BUILTIN_HANDLERS.get(action) or extra_handlers.get(action)
    if not handler:
        raise ValueError(
            f"action {action!r} has no handler — built-in: {sorted(_BUILTIN_HANDLERS)}, "
            "pass more via handler_overrides_json"
        )
    return handler


def _compile_terminal(root: ET.Element, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile a card whose action rounds are exhausted into a read-only terminal card.

    Called when ``round_ >= _MAX_ROUNDS`` — the 20 pre-registered action rounds
    (0..19) have all been consumed (Feishu tombstones each action id forever).
    Instead of clamping the round back to 19 and re-emitting the just-consumed
    ``_r19`` names (every button a dead key), the card becomes a static record:
    interactive elements degrade to passive info lines, no action is emitted,
    and the handlers map is empty. This mirrors the todo card's ``locked``
    terminal state — 轮次用尽即锁定为只读。
    """
    title = (root.get("title") or "").strip()
    template = _TEMPLATE_COLORS[(root.get("template") or "blue").strip()]
    elements: list[dict[str, Any]] = []
    for child in root:
        tag = child.tag
        if tag == "info":
            label = (child.get("label") or "").strip()
            value = (child.get("value") or "").strip()
            if not label or not value:
                raise ValueError("<info> requires label and value attributes")
            elements.append(_markdown_line(label, value))
        elif tag == "text":
            # 无标签纯文本段落(判断/证据/分数/底注等卡面文案);空值整段不渲染。
            text_el = _text_element(child)
            if text_el is not None:
                elements.append(text_el)
        elif tag == "candidate":
            # 终态:候选行退化为只读信息行(记入/不记状态),不再提供按钮。
            elements.extend(_candidate_block(child, _candidate_index(root, child), 0, context))
        elif tag == "score":
            selected = _parse_round(child.get("selected") or 0)
            elements.append(_markdown_line("评分", f"{selected} 分" if selected >= 1 else "未打分"))
        elif tag == "comment":
            comment_value = str(context.get("comment_value") or "").strip()
            if comment_value:
                elements.append(_markdown_line("评语", comment_value))
        elif tag in ("action-row", "list"):
            # 终态不再提供任何交互(action-row 按钮、list 行勾选全部退化)。
            continue
        else:
            raise ValueError(f"unknown element <{tag}> — vocabulary: info/score/comment/action-row/list under <card>")
    card: dict[str, Any] = {
        "schema": "2.0",
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "body": {"elements": elements},
    }
    return card, {}


def _compile(
    root: ET.Element, round_: int, context: dict[str, Any], extra_handlers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile a validated <card> tree into (feishu card 2.0 JSON, handlers)."""
    title = (root.get("title") or "").strip()
    template = _TEMPLATE_COLORS[(root.get("template") or "blue").strip()]

    elements: list[dict[str, Any]] = []
    handlers: dict[str, str] = {}
    # 同一 action 的交互元素重复出现计数:第二次起把序号折进 action_id,
    # 回调侧才能区分点的是哪个(评论框/按钮同理,见 _comment_input/_button_element)。
    action_occurrence: dict[str, int] = {}
    for index, child in enumerate(root):
        tag = child.tag
        if tag == "info":
            label = (child.get("label") or "").strip()
            value = (child.get("value") or "").strip()
            if not label or not value:
                raise ValueError("<info> requires label and value attributes")
            elements.append(_markdown_line(label, value))
        elif tag == "text":
            # 无标签纯文本段落(判断/证据/分数/底注等卡面文案);空值整段不渲染。
            text_el = _text_element(child)
            if text_el is not None:
                elements.append(text_el)
        elif tag == "candidate":
            # 候选确认行:展开为「候选文本+标签」段与 [记入]/[X 不记] 按钮对。
            # 行内动作 cand_{i}_yes/cand_{i}_no 需调用方经 handler_overrides_json
            # 提供(与 score/comment 的内置映射不同,行数运行时才知道)。
            # 已决定(recorded/rejected)的行只读、无按钮,也就无需注册 handler。
            index = _candidate_index(root, child)
            if (child.get("state") or "pending").strip() == "pending":
                yes_action = f"cand_{index}_yes"
                edit_action = f"cand_{index}_edit"
                no_action = f"cand_{index}_no"
                yes_handler = _resolve_handler(yes_action, extra_handlers)
                edit_handler = _resolve_handler(edit_action, extra_handlers)
                no_handler = _resolve_handler(no_action, extra_handlers)
                for r in range(_MAX_ROUNDS):
                    handlers[f"{yes_action}_r{r}"] = yes_handler
                    handlers[f"{edit_action}_r{r}"] = edit_handler
                    handlers[f"{no_action}_r{r}"] = no_handler
            elements.extend(_candidate_block(child, index, round_, context))
        elif tag == "score":
            # 轮次预注册:所有轮次的动作名 → 直调工具(六环之"映射")。
            # 预注册深度取 <score rounds="N">(XSD 声明的属性),缺省回落 _MAX_ROUNDS,
            # 不再对模板里声明的 rounds 视而不见(模板写 5 就该是 5 轮)。
            action = (child.get("action") or _DEFAULT_SCORE_ACTION).strip()
            handler = _resolve_handler(action, extra_handlers)
            rounds = _parse_rounds(child.get("rounds"))
            # rounds 是预注册的最小前瞻深度,但当前渲染轮次必须始终可路由:
            # round_ 被 _parse_round 钳到 [0, _MAX_ROUNDS-1],若 rounds < round_+1
            # (如模板声明 rounds=5 却已重建到第 6 轮),score 按钮会指向未注册的
            # handler 成死键。取 max 保证当前轮永远有 handler,同时仍尊重 rounds
            # 作为最小预注册深度(comment/button 恒注册满 _MAX_ROUNDS 故无此问题)。
            depth = max(rounds, round_ + 1)
            for r in range(depth):
                handlers[f"{action}_r{r}"] = handler
            occurrence = action_occurrence.get(action, 0)
            action_occurrence[action] = occurrence + 1
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "4px",
                    "background_style": "default",
                    "columns": _score_columns(child, round_, context, occurrence),
                }
            )
        elif tag == "comment":
            action = (child.get("action") or _DEFAULT_COMMENT_ACTION).strip()
            handler = _resolve_handler(action, extra_handlers)
            for r in range(_MAX_ROUNDS):
                handlers[f"{action}_r{r}"] = handler
            occurrence = action_occurrence.get(action, 0)
            action_occurrence[action] = occurrence + 1
            elements.append(_comment_input(child, round_, context, index, occurrence))
        elif tag == "action-row":
            columns: list[dict[str, Any]] = []
            for btn in child:
                if btn.tag != "button":
                    raise ValueError(f"<action-row> only holds <button>, got <{btn.tag}>")
                btn_action = (btn.get("action") or "").strip()
                # 先校验属性本身(action 必填、type 取值),报错指向属性而非 handler 缺失。
                if not btn_action:
                    raise ValueError("<button> requires an action attribute")
                type_raw = (btn.get("type") or "default").strip()
                if type_raw not in _BUTTON_TYPES:
                    raise ValueError(f"<button type={type_raw!r}> unknown — use accept/reject/danger/default/primary")
                btn_handler = _resolve_handler(btn_action, extra_handlers)
                for r in range(_MAX_ROUNDS):
                    handlers[f"{btn_action}_r{r}"] = btn_handler
                occurrence = action_occurrence.get(btn_action, 0)
                action_occurrence[btn_action] = occurrence + 1
                columns.append(
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [_button_element(btn, round_, context, occurrence)],
                    }
                )
            if not columns:
                # 空 action-row 与 XSD(button minOccurs=1)一致地报错,不静默吞掉。
                raise ValueError("<action-row> requires at least one <button>")
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "4px",
                    "background_style": "default",
                    "columns": columns,
                }
            )
        elif tag == "table":
            # 多维表格数据 → 卡片:引擎不做 I/O(保持纯净可离线测),行数据由调用方
            # 用 search_bitable_records 预取后注入 context[source]。<col field label>
            # 声明要展示哪些字段、显示成什么标签。每行渲成一个 markdown 块 + 分隔线。
            elements.extend(_table_blocks(child, context))
        elif tag == "divider":
            # 分割线:飞书 2.0 原生 hr,纯排版无交互。实卡验证通过(2026-08-27)。
            elements.append({"tag": "hr"})
        elif tag == "img":
            # 图片:飞书 2.0 原生 img,需 img-key(上传素材后飞书返回的 key)。
            # 实测飞书接受 img 元素格式(假 key 报 200570 invalid image key,非
            # unsupported tag),故格式有效;真实渲染需业务传入真 img-key。
            img_key = (child.get("img-key") or "").strip()
            if not img_key:
                raise ValueError("<img> requires an img-key attribute")
            alt = (child.get("alt") or "").strip()
            elements.append(
                {
                    "tag": "img",
                    "img_key": img_key,
                    "alt": {"tag": "plain_text", "content": alt},
                }
            )
        elif tag == "date":
            # 日期选择器:飞书 2.0 date_picker,点选即回调(与 score/comment 同样
            # 走轮次动作名 + 预注册 handler)。子回调 value 带选中日期(飞书注入)。
            action = (child.get("action") or "").strip()
            if not action:
                raise ValueError("<date> requires an action attribute")
            handler = _resolve_handler(action, extra_handlers)
            for r in range(_MAX_ROUNDS):
                handlers[f"{action}_r{r}"] = handler
            occurrence = action_occurrence.get(action, 0)
            action_occurrence[action] = occurrence + 1
            elements.append(_date_picker(child, round_, context, occurrence))
        elif tag == "select":
            # 下拉单选:飞书 2.0 select_static,option 子元素声明候选项。
            action = (child.get("action") or "").strip()
            if not action:
                raise ValueError("<select> requires an action attribute")
            handler = _resolve_handler(action, extra_handlers)
            for r in range(_MAX_ROUNDS):
                handlers[f"{action}_r{r}"] = handler
            occurrence = action_occurrence.get(action, 0)
            action_occurrence[action] = occurrence + 1
            elements.append(_select_static(child, round_, context, occurrence))
        else:
            raise ValueError(
                f"unknown element <{tag}> — vocabulary: "
                "info/score/comment/action-row/list/divider/img/date/select under <card>"
            )

    card: dict[str, Any] = {
        "schema": "2.0",
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "body": {"elements": elements},
    }
    return card, handlers


def _compile_list_card(root: ET.Element, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile a <list>-bearing card into the legacy todo-card shape.

    The todo card's multi-row tick/untick machinery already exists and is
    battle-tested in ``_todo_card_impl`` (row-level rounds, the self-contained
    ``card_state`` blob, per-row handler pre-registration). The DSL engine does
    not re-implement any of it: ``list``/``row`` just assemble the same state
    structure and hand it to ``_build_card_from_state`` — same card bytes, same
    callback tools, zero drift from the hand-written version.
    """
    list_el = next(child for child in root if child.tag == "list")
    # 行默认形状:行级 shape > 卡级 shape(context 注入,发卡工具的参数) >
    # list 元素属性 > circle。shape 走 context 而不是模板占位符,避免第三方
    # 渲染不传时把字面量 {shape} 填进属性。
    shape_default = str(context.get("shape") or list_el.get("shape") or "circle").strip() or "circle"
    rows: list[dict[str, Any]] = []
    for row_el in list_el:
        if row_el.tag != "row":
            raise ValueError(f"<list> only holds <row>, got <{row_el.tag}>")
        title = (row_el.get("title") or "").strip()
        if not title:
            raise ValueError("<row> requires a title attribute")
        # XSD 声明 done 为 xs:boolean,合法字面量是 true/false/1/0(大小写敏感按
        # XSD 规范,但 LLM 常写 True/TRUE,一并容错)。只认 =="true" 会把 done="1"
        # 静默当未完成,与 Schema 漂移;这里对齐 XSD 的布尔取值集。
        done = (row_el.get("done") or "").strip().lower() in ("true", "1")
        rows.append(
            {
                "title": title,
                "task_guid": (row_el.get("task-guid") or "").strip(),
                "detail": (row_el.get("detail") or "").strip(),
                "shape": (row_el.get("shape") or shape_default).strip(),
                "ledger_record_id": (row_el.get("bind-record") or "").strip(),
                "link": (row_el.get("link") or "").strip(),
                "done": done,
                "round": 0,
                # 发卡时已完成的行走只读(无按钮),与手写版 locked 语义一致。
                "locked": done,
            }
        )
    state = {
        "title": (root.get("title") or "").strip(),
        # subtitle 是数据不是结构,走 context 注入(发卡工具把副标题放这里,
        # 与 ledger_app_token/ledger_table_id 同路);模板 XML 不出现 {subtitle}。
        "subtitle": str(context.get("subtitle") or ""),
        "ledger_app_token": str(context.get("ledger_app_token") or ""),
        "ledger_table_id": str(context.get("ledger_table_id") or ""),
        "rows": rows,
    }
    card = _build_card_from_state(state)
    handlers: dict[str, str] = {}
    for index, row in enumerate(rows):
        if row["done"]:
            continue
        for r in range(_UNDO_ROUNDS):
            handlers[_tick_action_id(index, r)] = "feishu_todo_card_tick"
            handlers[_untick_action_id(index, r)] = "feishu_todo_card_untick"
    return card, handlers


# ── Templates(固定卡型定义,模块化预留)────────────────────────────────────────
#
# 模板 = 卡片的持久定义资产:固定卡型(评价卡/todo 卡)的 XML 骨架落成文件,
# 发卡时只填数据、不写结构(海豚不自由发挥,出错率大幅下降)。
# 这就是 Dustin 第 4 点"卡片定义可能放在数据库"的当前形态:定义与引擎分离,
# 将来定义挪进数据库,渲染入口(字符串进)与模板内容都不动。


def _resolve_template_dir() -> str:
    """Locate the card-dsl templates directory.

    Inside a Session the tool modules are loaded via compile+exec under a
    synthesized module name, so ``__file__`` is unreliable. Resolve through the
    runtime agent dir first (``_runtime_paths.agent_dir()``, the same root the
    skill loader uses), falling back to the ``__file__``-relative path that
    works when this module runs standalone (local tests).
    """
    candidates: list[str] = []
    with contextlib.suppress(Exception):
        candidates.append(os.path.join(agent_dir(), "skills", "card-dsl", "templates"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "card-dsl", "templates"))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[-1]


_TEMPLATE_DIR = _resolve_template_dir()


def _xml_escape(text: str) -> str:
    # 所有调用都把结果塞进双引号属性(title="{...}" / value="{...}"),saxutils.escape
    # 默认只转 < > &,不转引号——值里含 " 会截断属性、破坏整段 XML(标题/人名/评语
    # 常有引号)。显式把 " 和 ' 也转义,保证填充后的属性始终合法。
    return escape(text, {'"': "&quot;", "'": "&apos;"})



def _text_element(element: ET.Element) -> dict[str, Any] | None:
    """Compile a <text> element: a plain markdown paragraph (no label).

    Used for card copy that is not a key-value info line — verdict paragraphs,
    star scores, hints, footers. An empty value renders nothing.
    """
    value = (element.get("value") or "").strip()
    if not value:
        return None
    return {"tag": "markdown", "content": value}


def _candidate_index(root: ET.Element, child: ET.Element) -> int:
    """Ordinal of a <candidate> among its candidate siblings (0-based).

    ``candidate`` elements are direct children of ``<card>`` in the templates,
    so a linear scan over the root's children is enough.
    """
    index = 0
    for c in root:
        if c.tag == "candidate":
            if c is child:
                return index
            index += 1
    return index


def _candidate_block(element: ET.Element, index: int, round_: int, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Compile one <candidate> row: candidate text + tag + [记入][不记] buttons.

    A candidate row is a record-worthy behaviour sentence extracted from a
    transcript. While ``state`` is pending it carries a yes/no button pair whose
    actions are ``cand_{index}_yes`` / ``cand_{index}_no`` (round-suffixed);
    once recorded/rejected the row renders read-only with a status note.
    """
    title = (element.get("title") or "").strip()
    if not title:
        raise ValueError("<candidate> requires a title attribute")
    tag = (element.get("tag") or "").strip()
    side = (element.get("side") or "").strip()
    state = (element.get("state") or "pending").strip()
    if state not in ("pending", "recorded", "rejected"):
        raise ValueError(f"<candidate state={state!r}> unknown — use pending/recorded/rejected")

    body = title if not tag else f"{title}\n{tag}"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": body}]
    if state == "pending":
        buttons: list[dict[str, Any]] = []
        for action, text, type_raw in (
            (f"cand_{index}_yes", "计入", "accept"),
            (f"cand_{index}_no", "不计", "reject"),
            (f"cand_{index}_edit", "改判", "primary"),
        ):
            value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
            value["index"] = index
            value["side"] = side
            value["action_id"] = f"{action}_r{round_}"
            buttons.append(
                {
                    "tag": "column",
                    "width": "auto",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": text},
                            "type": _BUTTON_TYPES[type_raw],
                            "behaviors": [{"type": "callback", "value": value}],
                        }
                    ],
                }
            )
        elements.append(
            {
                "tag": "column_set",
                "flex_mode": "none",
                "horizontal_spacing": "4px",
                "background_style": "default",
                "columns": buttons,
            }
        )
    else:
        status_text = "✓ 已记录" if state == "recorded" else "✗ 未记录"
        elements.append({"tag": "markdown", "content": status_text})
    return elements


def _pick_xml(row: dict[str, Any]) -> str:
    """Serialize one {picks} entry into a <candidate .../> element.

    Candidate titles may contain ``{word}`` text; they are XML-entity escaped so
    the template placeholder pass cannot corrupt them.
    """
    attrs: list[str] = []
    for key, xml_attr in (("title", "title"), ("tag", "tag"), ("side", "side"), ("bind_record", "bind-record")):
        val = row.get(key)
        if val is not None and str(val) != "":
            safe = _xml_escape(str(val)).replace("{", "&#123;").replace("}", "&#125;")
            attrs.append(f'{xml_attr}="{safe}"')
    if row.get("state"):
        attrs.append(f'state="{row["state"]}"')
    return f"<candidate {' '.join(attrs)}/>"

def _row_xml(row: dict[str, Any]) -> str:
    """Serialize one {rows} entry into a <row .../> element with escaped attrs.

    键名兼容两套:文档写的 ``bind_record``(模板/调用方视角)与 list 卡编译路径
    内部用的 ``ledger_record_id``(_build_card_from_state 的状态字段)——两套并存时
    若只认其一,另一套会被静默忽略,行数据悄悄丢。前者优先,后者兜底。
    """
    attrs: list[str] = []
    for key, xml_attr in (
        ("title", "title"),
        ("task_guid", "task-guid"),
        ("detail", "detail"),
        ("shape", "shape"),
        ("bind_record", "bind-record"),
        ("link", "link"),
    ):
        val = row.get(key)
        if val is None and key == "bind_record":
            val = row.get("ledger_record_id")
        if val:
            attrs.append(f'{xml_attr}="{_xml_escape(str(val))}"')
    if row.get("done"):
        attrs.append('done="true"')
    return f"<row {' '.join(attrs)}/>"


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _fill_template(xml: str, values: dict[str, Any]) -> str:
    """Fill ``{key}`` placeholders in a single pass.

    ``{rows}`` expands a list of row dicts; ``{note}`` becomes a status info line
    when non-empty and vanishes otherwise; every other ``{key}`` is replaced by
    its XML-escaped value. A single-pass ``re.sub`` is used deliberately: the old
    chained ``str.replace`` re-scanned already-substituted text, so a value that
    happened to contain ``{another_key}`` would be expanded again (one field
    could bleed into another). Here each placeholder is resolved exactly once
    against the original ``values`` and substituted text is never rescanned.
    Unknown placeholders are left intact so template bugs stay visible.
    """
    rows = values.get("rows")
    rows_xml = "\n".join(_row_xml(r) for r in rows if isinstance(r, dict)) if isinstance(rows, list) else None
    picks = values.get("picks")
    picks_xml = (
        "\n".join(_pick_xml(r) for r in picks if isinstance(r, dict)) if isinstance(picks, list) else None
    )
    note = str(values.get("note", "") or "").strip()
    note_xml = f'<info label="状态" value="{_xml_escape(note)}"/>' if note else ""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "rows":
            return rows_xml if rows_xml is not None else match.group(0)
        if key == "picks":
            return picks_xml if picks_xml is not None else match.group(0)
        if key == "note":
            return note_xml
        if key not in values:
            # Unknown key: leave the literal placeholder so the gap is obvious.
            return match.group(0)
        val = values[key]
        return _xml_escape("" if val is None else str(val))

    return _PLACEHOLDER_RE.sub(_replace, xml)


def render_template(
    template_name: str,
    values_json: str = "{}",
    context_json: str = "{}",
    round_: int = 0,
    handler_overrides_json: str = "{}",
) -> dict[str, Any]:
    """Load a fixed card template, fill its placeholders, and compile it.

    Templates live under ``skills/card-dsl/templates/``. Returns the same shape
    as ``render_card`` — ``{"ok": True, "card": ..., "handlers": ...}`` or an
    error dict.
    """
    name = (template_name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return {"ok": False, "error": "invalid template_name"}
    path = os.path.join(_TEMPLATE_DIR, f"{name}.xml")
    try:
        with open(path, encoding="utf-8") as f:
            xml = f.read()
    except OSError as e:
        return {"ok": False, "error": f"template {name!r} not found: {e!r}"}
    try:
        values = json.loads(values_json) if isinstance(values_json, str) else values_json
    except ValueError:
        return {"ok": False, "error": "values_json is not valid JSON"}
    if not isinstance(values, dict):
        return {"ok": False, "error": "values_json must be a JSON object"}
    filled = _fill_template(xml, dict(values))
    # 残留占位符 = 模板要求的键没传全。填充后若还有 {key},回调 value 会拿到
    # 字面量脏值(如 record_id="{record_id}"),点击后拿着假 id 写台账——
    # 在这里报错,而不是把脏卡发出去。(值里恰好含 {word} 的合法文本会被误报,
    # 概率极低,且报错可修、脏值静默产错不可修,取前者。)
    # 模板顶部注释常写「{key} 为占位符」说明文字,那只是文档不是真占位符,
    # 扫描前剔除 <!-- --> 注释,只检查卡片内容里的残留。
    body_no_comments = re.sub(r"<!--.*?-->", "", filled, flags=re.DOTALL)
    leftover = sorted(set(_PLACEHOLDER_RE.findall(body_no_comments)))
    if leftover:
        keys = "、".join(f"{{{k}}}" for k in leftover)
        return {
            "ok": False,
            "error": (f"template {name!r} 有未填充的占位符: {keys} —— values_json 缺这些键,不会发出脏卡"),
        }
    return render_card(
        card_xml=filled,
        context_json=context_json,
        round_=round_,
        handler_overrides_json=handler_overrides_json,
    )


# ── Public entry ──────────────────────────────────────────────────────────────


def render_card(
    card_xml: str,
    context_json: str = "{}",
    round_: int = 0,
    handler_overrides_json: str = "{}",
) -> dict[str, Any]:
    """Compile a DSL declaration into a Feishu card 2.0 JSON + action handlers.

    Returns ``{"ok": True, "card": {...}, "handlers": {...}}`` or
    ``{"ok": False, "error": ...}``. Never raises for caller-side mistakes —
    unknown elements/attributes come back as errors, not exceptions.
    """
    root, error = _validate(card_xml)
    if error or root is None:
        return {"ok": False, "error": error or "validation failed without detail"}
    try:
        context = json.loads(context_json) if isinstance(context_json, str) else context_json
    except ValueError:
        return {"ok": False, "error": "context_json is not valid JSON"}
    if not isinstance(context, dict):
        return {"ok": False, "error": "context_json must be a JSON object"}
    try:
        overrides = (
            json.loads(handler_overrides_json) if isinstance(handler_overrides_json, str) else handler_overrides_json
        )
    except ValueError:
        return {"ok": False, "error": "handler_overrides_json is not valid JSON"}
    if not isinstance(overrides, dict):
        return {"ok": False, "error": "handler_overrides_json must be a JSON object"}
    try:
        round_int = int(round_)
    except (TypeError, ValueError):
        round_int = 0
    round_ = max(0, min(round_int, _MAX_ROUNDS - 1))
    # overrides 先并入编译,自定义 action 在编译阶段就能解析到 handler。
    extra_handlers: dict[str, str] = {
        action: handler
        for action, handler in overrides.items()
        if isinstance(action, str) and isinstance(handler, str) and action and handler
    }
    try:
        list_count = sum(1 for child in root if child.tag == "list")
        if list_count:
            # 多个 <list> 是 XSD choice(maxOccurs=unbounded)允许的输入,但编译
            # 路径只取第一个——第二个会静默丢行。数量 != 1 时明确报错,不吞数据。
            if list_count != 1:
                raise ValueError(f"<card> 只能有一个 <list>,当前声明了 {list_count} 个")
            # list 卡走 legacy todo-card 路径(多行逐条勾选),复用已验证的行机制;
            # 第一版只支持 <list>(info 等其余元素混用暂不支持,报错而非静默忽略)。
            for child in root:
                if child.tag != "list":
                    raise ValueError(f"list 卡暂只支持 <list> 元素,不支持 <{child.tag}>")
            card, handlers = _compile_list_card(root, dict(context))
        else:
            if round_int >= _MAX_ROUNDS:
                # 轮次用尽(>= 20):终态只读卡,不再发任何 action——否则 round 重新
                # 钳回 19,动作名与第 20 轮已消费的撞车,重建后的按钮全是死键。
                card, handlers = _compile_terminal(root, dict(context))
            else:
                card, handlers = _compile(root, round_, dict(context), extra_handlers)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "card": card, "handlers": handlers}
