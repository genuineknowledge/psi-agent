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

import json
import os
import xml.etree.ElementTree as ET
from typing import Any

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
}

# 轮次上限:飞书 action 单卡单次消费,每重建一次轮次 +1 生成全新 action,
# 卡片才能反复操作;预注册 _MAX_ROUNDS 轮(与评价卡实卡验证一致)。
_MAX_ROUNDS = 20


def _parse_round(raw: Any) -> int:
    try:
        return max(0, min(int(raw), _MAX_ROUNDS - 1))
    except (TypeError, ValueError):
        return 0


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(card_xml: str) -> tuple[dict[str, Any] | None, str]:
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
    return {"tag": "markdown", "content": f"**{label}**：{value}"}


def _base_value(
    element: ET.Element, action: str, score: int, round_: int, context: dict[str, Any]
) -> dict[str, Any]:
    """Assemble the callback value shared by one interactive element.

    = spec 3.2.2 ring 4 (回调组装):``bind-record`` + caller context + action +
    score + round are merged into the value. ``bind-record`` wins over a context
    ``record_id``; context supplies the rest (owner/title/cycle/task_guid/...).
    """
    value: dict[str, Any] = dict(context)
    bind_record = (element.get("bind-record") or "").strip()
    if bind_record:
        value["record_id"] = bind_record
    value["action"] = action
    value["round"] = round_
    if score:
        value["score"] = score
    return value


def _score_columns(
    element: ET.Element, round_: int, context: dict[str, Any]
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
        action_id = f"{action}_{score}_r{round_}"
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
    element: ET.Element, round_: int, context: dict[str, Any]
) -> dict[str, Any]:
    action = (element.get("action") or _DEFAULT_COMMENT_ACTION).strip()
    placeholder = (element.get("placeholder") or "写点评语（可选）").strip()
    comment_value = str(context.get("comment_value") or "")
    value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
    value["action_id"] = f"{action}_r{round_}"
    record_id = str(value.get("record_id") or "r")
    input_el: dict[str, Any] = {
        # confirm 字段让输入框带出「确认」按钮:点确认才把输入文字带回回调
        # (不带 confirm 的 input 输入后没有任何事件,值收不到)。
        "tag": "input",
        "input_type": "text",
        "name": f"comment_{record_id}",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        # value 是 input 的初始文本:重建时把上次评语带回输入框,可继续编辑。
        "value": comment_value,
        "confirm": {
            "title": {"tag": "plain_text", "content": "确认评语"},
            "text": {"tag": "plain_text", "content": "把这条评语写入台账？"},
        },
        "behaviors": [{"type": "callback", "value": value}],
    }
    return input_el


def _button_element(
    element: ET.Element, round_: int, context: dict[str, Any]
) -> dict[str, Any]:
    text = (element.get("text") or "").strip()
    if not text:
        text = "按钮"
    type_raw = (element.get("type") or "default").strip()
    feishu_type = _BUTTON_TYPES.get(type_raw)
    if feishu_type is None:
        raise ValueError(
            f"<button type={type_raw!r}> unknown — use accept/reject/danger/default/primary"
        )
    action = (element.get("action") or "").strip()
    if not action:
        raise ValueError("<button> requires an action attribute")
    action_id = f"{action}_r{round_}"
    value = _base_value(element, f"{action}_r{round_}", 0, round_, context)
    value["action_id"] = action_id
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": feishu_type,
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


def _compile(
    root: ET.Element, round_: int, context: dict[str, Any], extra_handlers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile a validated <card> tree into (feishu card 2.0 JSON, handlers)."""
    title = (root.get("title") or "").strip()
    template = _TEMPLATE_COLORS[(root.get("template") or "blue").strip()]

    elements: list[dict[str, Any]] = []
    handlers: dict[str, str] = {}
    for child in root:
        tag = child.tag
        if tag == "info":
            label = (child.get("label") or "").strip()
            value = (child.get("value") or "").strip()
            if not label or not value:
                raise ValueError("<info> requires label and value attributes")
            elements.append(_markdown_line(label, value))
        elif tag == "score":
            # 轮次预注册:所有轮次的动作名 → 直调工具(六环之"映射")。
            action = (child.get("action") or _DEFAULT_SCORE_ACTION).strip()
            handler = _resolve_handler(action, extra_handlers)
            for r in range(_MAX_ROUNDS):
                handlers[f"{action}_r{r}"] = handler
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "4px",
                    "background_style": "default",
                    "columns": _score_columns(child, round_, context),
                }
            )
        elif tag == "comment":
            action = (child.get("action") or _DEFAULT_COMMENT_ACTION).strip()
            handler = _resolve_handler(action, extra_handlers)
            for r in range(_MAX_ROUNDS):
                handlers[f"{action}_r{r}"] = handler
            elements.append(_comment_input(child, round_, context))
        elif tag == "action-row":
            columns: list[dict[str, Any]] = []
            for btn in child:
                if btn.tag != "button":
                    raise ValueError(f"<action-row> only holds <button>, got <{btn.tag}>")
                btn_action = (btn.get("action") or "").strip()
                # 先校验 type 取值,报错指向属性本身而非 handler 缺失。
                type_raw = (btn.get("type") or "default").strip()
                if type_raw not in _BUTTON_TYPES:
                    raise ValueError(
                        f"<button type={type_raw!r}> unknown — use accept/reject/danger/default/primary"
                    )
                btn_handler = _resolve_handler(btn_action, extra_handlers)
                for r in range(_MAX_ROUNDS):
                    handlers[f"{btn_action}_r{r}"] = btn_handler
                columns.append(
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [_button_element(btn, round_, context)],
                    }
                )
            if columns:
                elements.append(
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "horizontal_spacing": "4px",
                        "background_style": "default",
                        "columns": columns,
                    }
                )
        else:
            raise ValueError(f"unknown element <{tag}> — vocabulary: card/info/score/comment/action-row/button")

    card: dict[str, Any] = {
        "schema": "2.0",
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "body": {"elements": elements},
    }
    return card, handlers


def _compile_list_card(
    root: ET.Element, context: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile a <list>-bearing card into the legacy todo-card shape.

    The todo card's multi-row tick/untick machinery already exists and is
    battle-tested in ``_todo_card_impl`` (row-level rounds, the self-contained
    ``card_state`` blob, per-row handler pre-registration). The DSL engine does
    not re-implement any of it: ``list``/``row`` just assemble the same state
    structure and hand it to ``_build_card_from_state`` — same card bytes, same
    callback tools, zero drift from the hand-written version.
    """
    from _todo_card_impl import (
        _UNDO_ROUNDS,
        _build_card_from_state,
        _tick_action_id,
        _untick_action_id,
    )

    list_el = next(child for child in root if child.tag == "list")
    shape_default = (list_el.get("shape") or "circle").strip()
    rows: list[dict[str, Any]] = []
    for row_el in list_el:
        if row_el.tag != "row":
            raise ValueError(f"<list> only holds <row>, got <{row_el.tag}>")
        title = (row_el.get("title") or "").strip()
        if not title:
            raise ValueError("<row> requires a title attribute")
        done = (row_el.get("done") or "").strip() == "true"
        rows.append(
            {
                "title": title,
                "task_guid": (row_el.get("task-guid") or "").strip(),
                "detail": (row_el.get("detail") or "").strip(),
                "shape": (row_el.get("shape") or shape_default).strip(),
                "ledger_record_id": (row_el.get("bind-record") or "").strip(),
                "done": done,
                "round": 0,
                # 发卡时已完成的行走只读(无按钮),与手写版 locked 语义一致。
                "locked": done,
            }
        )
    state = {
        "title": (root.get("title") or "").strip(),
        "subtitle": "",
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
    try:
        from _runtime_paths import agent_dir

        candidates.append(os.path.join(agent_dir(), "skills", "card-dsl", "templates"))
    except Exception:
        pass
    candidates.append(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "card-dsl", "templates")
    )
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[-1]


_TEMPLATE_DIR = _resolve_template_dir()


def _xml_escape(text: str) -> str:
    from xml.sax.saxutils import escape

    return escape(text)


def _row_xml(row: dict[str, Any]) -> str:
    """Serialize one {rows} entry into a <row .../> element with escaped attrs."""
    attrs: list[str] = []
    for key, xml_attr in (
        ("title", "title"),
        ("task_guid", "task-guid"),
        ("detail", "detail"),
        ("shape", "shape"),
        ("bind_record", "bind-record"),
    ):
        val = row.get(key)
        if val:
            attrs.append(f'{xml_attr}="{_xml_escape(str(val))}"')
    if row.get("done"):
        attrs.append('done="true"')
    return f"<row {' '.join(attrs)}/>"


def _fill_template(xml: str, values: dict[str, Any]) -> str:
    """Fill {key} placeholders; {rows} expands a list of row dicts; {note} becomes
    a status info line when non-empty and vanishes otherwise."""
    filled = xml
    if isinstance(values.get("rows"), list):
        rows_xml = "\n".join(_row_xml(r) for r in values["rows"] if isinstance(r, dict))
        filled = filled.replace("{rows}", rows_xml)
    note = str(values.pop("note", "") or "").strip()
    note_xml = f'<info label="状态" value="{_xml_escape(note)}"/>' if note else ""
    filled = filled.replace("{note}", note_xml)
    for key, val in values.items():
        if key == "rows":
            continue
        if val is None:
            val = ""
        filled = filled.replace("{" + key + "}", _xml_escape(str(val)))
    return filled


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
    if error:
        return {"ok": False, "error": error}
    try:
        context = json.loads(context_json) if isinstance(context_json, str) else context_json
    except ValueError:
        return {"ok": False, "error": "context_json is not valid JSON"}
    if not isinstance(context, dict):
        return {"ok": False, "error": "context_json must be a JSON object"}
    try:
        overrides = (
            json.loads(handler_overrides_json)
            if isinstance(handler_overrides_json, str)
            else handler_overrides_json
        )
    except ValueError:
        return {"ok": False, "error": "handler_overrides_json is not valid JSON"}
    if not isinstance(overrides, dict):
        return {"ok": False, "error": "handler_overrides_json must be a JSON object"}
    round_ = _parse_round(round_)
    # overrides 先并入编译,自定义 action 在编译阶段就能解析到 handler。
    extra_handlers: dict[str, str] = {
        action: handler
        for action, handler in overrides.items()
        if isinstance(action, str) and isinstance(handler, str) and action and handler
    }
    try:
        has_list = any(child.tag == "list" for child in root)
        if has_list:
            # list 卡走 legacy todo-card 路径(多行逐条勾选),复用已验证的行机制;
            # 第一版只支持 <list>(info 等其余元素混用暂不支持,报错而非静默忽略)。
            for child in root:
                if child.tag != "list":
                    raise ValueError(
                        f"list 卡暂只支持 <list> 元素,不支持 <{child.tag}>"
                    )
            card, handlers = _compile_list_card(root, dict(context))
        else:
            card, handlers = _compile(root, round_, dict(context), extra_handlers)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "card": card, "handlers": handlers}
