"""Shared implementation for the TODO-list card tools: rendering, card-action parsing,
row splicing, and ledger sync.

Kept in its own leading-underscore module (not registered as a tool, see
``tool_registry.py``'s discovery loop) so ``feishu_todo_card.py`` /
``feishu_todo_card_tick.py`` / ``feishu_todo_card_untick.py`` — three separately
*discovered* tool files — can share it via a normal import instead of importing each
other. Tool files are individually compiled and exec'd under synthetic module names by
the discovery loop; nothing in that mechanism guarantees a plain ``import
feishu_todo_card`` from inside another discovered tool file resolves to the same loaded
copy (or reliably at all). Underscore-prefixed files are skipped by discovery and behave
like any other importable module on ``sys.path``, which is the pattern every other
``_xxx_impl.py`` in this directory already relies on — this file follows it rather than
inventing a new one.

## Why every button carries the WHOLE card's state, not just its own row

The first version of this rewrite rebuilt only the clicked row by splicing it into
``payload["card"]`` — the card snapshot the Channel hands back with the click event.
That snapshot turned out to be **wrong for anything after the first click on a card**:
the Channel applies its own generic single-row "consumed" placeholder to a card
*before* dispatching to this tool, and persists THAT (not this tool's later, nicer
edit) as the snapshot behind the next click. So ticking row 0 then row 1 would silently
regress row 0's already-applied 「撤销」 button back to the Channel's plain placeholder —
confirmed by replaying real captured click payloads. Trusting Feishu's own snapshot for
multi-row state is fundamentally unsafe here.

The fix: every row's button embeds a ``card_state`` blob in its ``value`` — the full,
self-contained truth for **every row on the card** (title/task_guid/detail/shape/
ledger_record_id/done/round), plus the card-level title/subtitle/ledger target. A click
never needs Feishu's snapshot: it parses its own button's ``card_state``, updates just
the clicked row's ``done``/``round``, and rebuilds the **entire card** from that state —
which means every *other* row's button also gets freshly re-embedded with the same
updated ``card_state``, so whichever row is clicked next is looking at current truth,
not something the Channel silently overwrote.

Trade-off: this duplicates the full row list into every active button's payload, so
card size grows roughly with item_count², not item_count. For the realistic size this
module is built for (a handful to a couple dozen todos) that is a few KB — fine. It
would not scale gracefully to close to ``_MAX_ITEMS`` (40) all open at once.

Cards sent before this existed have no ``card_state`` in their buttons' values — those
fall back to the old single-row splice against ``payload["card"]``, which does **not**
have this multi-row protection. That is a known, already-communicated limitation for
cards already in flight; not fixable after the fact (a Feishu card's dispatch table is
frozen at send time, so those buttons cannot be made to carry a field they never had).
"""

from __future__ import annotations

import json
import re
from typing import Any

import _feishu_impl as _f

# 形状字符: 用文本承载勾选框, 因为框架只把 action/button/form 当交互元素,
# 飞书原生 checker 不在其中(点了不会被消费机制识别)。空心=未完成, 实心=已完成。
_SHAPES: dict[str, tuple[str, str]] = {
    "circle": ("○", "●"),
    "square": ("□", "■"),
    "diamond": ("◇", "◆"),
    "triangle": ("△", "▲"),
    "star": ("☆", "★"),
    "check": ("☐", "☑"),
}
_DEFAULT_SHAPE = "circle"
_TICK_PREFIX = "todo_tick"
_UNTICK_PREFIX = "todo_untick"
_MAX_ITEMS = 40
# 飞书卡片的一个 action_id 点过一次就永久失效(墓碑机制), 且编辑已发出的卡片不能给它
# 追加新的 id——所以"撤销后还能再标完成"必须在发卡那一刻就把每一轮会用到的 id 全部
# 预注册好。20 轮来回切换对"手滑点错"这种场景足够宽裕, 用完即锁定为已完成态、不再
# 出现按钮, 不是隐藏截断——两个工具在到顶时都会保持这个锁定态, 不会报错也不会装作
# 还能继续。
_UNDO_ROUNDS = 20

_ACTION_ID_RE = re.compile(r"^todo_(tick|untick)_(\d+)(?:_r(\d+))?$")


def _shape_chars(shape: str) -> tuple[str, str]:
    return _SHAPES.get(shape.strip().casefold() or _DEFAULT_SHAPE, _SHAPES[_DEFAULT_SHAPE])


def _tick_action_id(index: int, round_: int) -> str:
    return f"{_TICK_PREFIX}_{index}_r{round_}"


def _untick_action_id(index: int, round_: int) -> str:
    return f"{_UNTICK_PREFIX}_{index}_r{round_}"


def _round_of(action_id: str) -> int:
    """The round embedded in an action id. A bare legacy id (no ``_r{n}`` suffix, from a
    card sent before undo support existed) is treated as round 0.
    """
    match = _ACTION_ID_RE.match(action_id)
    if not match or match.group(3) is None:
        return 0
    return int(match.group(3))


def _task_url(task_guid: str, link: str) -> str:
    """The clickable target for a row: an explicit link wins, else the task applink."""
    if link.strip():
        return link.strip()
    if task_guid.strip():
        # applink 是飞书官方的客户端跳转协议, 不依赖任务接口返回 web url
        # (task/v2 的返回体里没有 url 字段, 别去等它)。
        return f"https://applink.feishu.cn/client/todo/detail?guid={task_guid.strip()}"
    return ""


def _row_elements(
    item: dict[str, Any],
    index: int,
    shape_override: str,
    *,
    done: bool,
    action: tuple[str, int] | None,
    state_json: str = "",
) -> list[dict[str, Any]]:
    """Render one row's markdown + (optional) button.

    ``action`` is ``("tick", round)`` / ``("untick", round)`` for a row that should still
    offer a button, or ``None`` for a locked/terminal row (pre-done items, or a row that
    has used up all ``_UNDO_ROUNDS``).

    ``state_json`` is the whole-card ``card_state`` blob (see module docstring) to embed
    in the button's value — pass it whenever called from ``_build_card_from_state``.
    Leaving it empty renders the OLD per-field value shape instead (``detail``/``shape``/
    ``ledger_*`` embedded directly, no ``card_state``); that shape only exists for the
    legacy single-row splice fallback in ``feishu_todo_card_tick``/``_untick``, kept for
    cards sent before ``card_state`` existed — new code should always pass ``state_json``.
    """
    title = str(item.get("title") or "").strip() or f"任务 {index + 1}"
    shape = str(item.get("shape") or shape_override or _DEFAULT_SHAPE)
    empty, filled = _shape_chars(shape)
    task_guid = str(item.get("task_guid") or "")
    url = _task_url(task_guid, str(item.get("link") or ""))
    detail = str(item.get("detail") or "").strip()

    label = f"[{title}]({url})" if url else title
    lines = [f"{filled} ~~{label}~~"] if done else [f"{empty} **{label}**"]
    if detail:
        lines.append(detail)
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n".join(lines)}]
    if action is not None:
        kind, round_ = action
        action_id = _tick_action_id(index, round_) if kind == "tick" else _untick_action_id(index, round_)
        button_text = f"{empty} 标记完成" if kind == "tick" else "撤销"
        value: dict[str, Any] = {
            "action": action_id,
            "todo_index": index,
            "todo_title": title,
            "task_guid": task_guid,
        }
        if state_json:
            value["card_state"] = state_json
        else:
            value.update(
                {
                    "detail": detail,
                    "shape": shape,
                    "ledger_record_id": str(item.get("ledger_record_id") or ""),
                    "ledger_app_token": str(item.get("ledger_app_token") or ""),
                    "ledger_table_id": str(item.get("ledger_table_id") or ""),
                }
            )
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": button_text},
                        "type": "default",
                        "value": value,
                    }
                ],
            }
        )
    return elements


def _serialize_card_state(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _parse_card_state(state_json: str) -> dict[str, Any] | None:
    try:
        state = json.loads(state_json)
    except ValueError:
        return None
    if not isinstance(state, dict) or not isinstance(state.get("rows"), list):
        return None
    return state


def _build_card_from_state(state: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the ENTIRE card from a self-contained state blob (see module docstring).

    Every row is regenerated from ``state["rows"]`` — including rows nobody just
    clicked — and every button on the rebuilt card gets the SAME freshly re-serialized
    ``card_state`` embedded. That is what keeps the next click (on any row) working from
    current truth instead of whatever the Channel's own snapshot says.

    A row with ``locked: true`` (set once, at send time, for items that were already
    ``done`` before entering this tick/untick flow — see ``_build_todo_card``) never gets
    a button, no matter what ``done``/``round`` say: it has no registered handlers to
    dispatch to, so offering one would be a dead click. This is also what disambiguates
    "started done, never interactive" from "ticked once at round 0" — both otherwise look
    identical (``done=True, round=0``).
    """
    rows_raw = state.get("rows")
    rows: list[dict[str, Any]] = (
        [row for row in rows_raw if isinstance(row, dict)] if isinstance(rows_raw, list) else []
    )
    title = str(state.get("title") or "")
    subtitle = str(state.get("subtitle") or "")
    done_count = sum(1 for row in rows if row.get("done"))
    header_lines = [f"进度: {done_count}/{len(rows)} 已完成"]
    if subtitle.strip():
        header_lines.insert(0, subtitle.strip())

    state_json = _serialize_card_state(state)

    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n".join(header_lines)}]
    for index, row in enumerate(rows):
        elements.append({"tag": "hr"})
        done = bool(row.get("done"))
        round_ = int(row.get("round") or 0)
        item = {
            "title": row.get("title"),
            "task_guid": row.get("task_guid"),
            "detail": row.get("detail"),
            "shape": row.get("shape"),
        }
        if row.get("locked"):
            action = None
        elif done:
            action = ("untick", round_) if round_ < _UNDO_ROUNDS - 1 else None
        else:
            action = ("tick", round_) if round_ < _UNDO_ROUNDS else None
        elements.extend(_row_elements(item, index, "", done=done, action=action, state_json=state_json))

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title.strip() or "今日 TODO"},
            "template": "green" if done_count == len(rows) and rows else "blue",
        },
        "elements": elements,
    }


def _build_todo_card(
    *,
    items: list[dict[str, Any]],
    title: str,
    subtitle: str,
    shape: str,
    ledger_app_token: str = "",
    ledger_table_id: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    rows: list[dict[str, Any]] = [
        {
            "title": str(item.get("title") or ""),
            "task_guid": str(item.get("task_guid") or ""),
            "detail": str(item.get("detail") or ""),
            "shape": str(item.get("shape") or shape or _DEFAULT_SHAPE),
            "ledger_record_id": str(item.get("ledger_record_id") or ""),
            "done": bool(item.get("done")),
            "round": 0,
            "locked": bool(item.get("done")),
        }
        for item in items
    ]
    state = {
        "title": title,
        "subtitle": subtitle,
        "ledger_app_token": ledger_app_token,
        "ledger_table_id": ledger_table_id,
        "rows": rows,
    }
    card = _build_card_from_state(state)

    handlers: dict[str, str] = {}
    for index, row in enumerate(rows):
        if row["done"]:
            continue
        for round_ in range(_UNDO_ROUNDS):
            handlers[_tick_action_id(index, round_)] = "feishu_todo_card_tick"
            handlers[_untick_action_id(index, round_)] = "feishu_todo_card_untick"
    return card, handlers


def _parse_action(card_action_json: str) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(card_action_json, str) or not card_action_json.strip():
        return None, "[Error] card_action_json is required (pass the <feishu_card_action> payload)"
    try:
        payload = json.loads(card_action_json)
    except ValueError as exc:
        return None, f"[Error] card_action_json is not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "[Error] card_action_json must be a JSON object"
    return payload, ""


def _action_value(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _rebuild_row_in_card(card: dict[str, Any], index: int, row_elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Splice a freshly rendered row into the card at its ``hr``-delimited slot.

    LEGACY-ONLY (see module docstring): used exclusively by the fallback path in
    ``_apply_row_transition`` for cards sent before ``card_state`` existed. Does not
    protect against a later click's rebuild regressing an earlier click's row — that is
    the exact bug ``card_state`` exists to fix, and this splice-against-Feishu's-own-
    snapshot approach cannot be made safe against it after the fact.

    Rows are separated by ``{"tag": "hr"}`` dividers in send order (see
    ``_build_card_from_state``): the header markdown comes first, then one ``hr`` + row
    per item. The ``index``-th ``hr`` marks the start of that row's slot, running until
    the next ``hr`` or the end of the element list.
    """
    elements = card.get("elements")
    if not isinstance(elements, list):
        return None
    hr_positions = [i for i, el in enumerate(elements) if isinstance(el, dict) and el.get("tag") == "hr"]
    if index >= len(hr_positions):
        return None
    start = hr_positions[index] + 1
    end = hr_positions[index + 1] if index + 1 < len(hr_positions) else len(elements)
    new_elements = elements[:start] + row_elements + elements[end:]
    return {**card, "elements": new_elements}


def _prepare_row_transition(
    *,
    payload: dict[str, Any],
    value: dict[str, Any],
    index: int,
    new_done: bool,
    new_round: int,
    fallback_title: str,
    fallback_task_guid: str,
) -> tuple[str, dict[str, Any] | None]:
    """Build the card content for a row transition — pure, no Feishu request.

    Same logic as ``_apply_row_transition``, split out so callers can send the
    ``edit_card`` **concurrently** with the task PATCH / ledger update instead of
    chaining three serial Feishu round-trips behind every click. Returns
    ``(status, new_card)``; ``new_card`` is None exactly when nothing should be pushed.
    """
    state_json = value.get("card_state")
    if isinstance(state_json, str) and state_json.strip():
        state = _parse_card_state(state_json)
        if state is None:
            return "skipped_invalid_card_state", None
        rows = state.get("rows")
        if not isinstance(rows, list) or index >= len(rows) or not isinstance(rows[index], dict):
            return "skipped_row_index_out_of_range", None
        rows[index] = {**rows[index], "done": new_done, "round": new_round}
        return "ok", _build_card_from_state(state)

    current_card = payload.get("card")
    if not isinstance(current_card, dict):
        return "skipped_missing_card_in_payload", None
    item = {
        "title": fallback_title,
        "task_guid": fallback_task_guid,
        "detail": value.get("detail") or "",
        "shape": value.get("shape") or "",
        "ledger_record_id": value.get("ledger_record_id") or "",
        "ledger_app_token": value.get("ledger_app_token") or "",
        "ledger_table_id": value.get("ledger_table_id") or "",
    }
    action: tuple[str, int] | None = None
    if new_done and new_round < _UNDO_ROUNDS - 1:
        action = ("untick", new_round)
    elif not new_done and new_round < _UNDO_ROUNDS:
        action = ("tick", new_round)
    row_elements = _row_elements(item, index, "", done=new_done, action=action)
    rebuilt = _rebuild_row_in_card(current_card, index, row_elements)
    if rebuilt is None:
        return "skipped_row_slot_not_found", None
    return "ok", rebuilt


async def _apply_row_transition(
    *,
    message_id: str,
    payload: dict[str, Any],
    value: dict[str, Any],
    index: int,
    new_done: bool,
    new_round: int,
    fallback_title: str,
    fallback_task_guid: str,
    user_key: str,
) -> str:
    """Move row ``index`` to ``(new_done, new_round)`` and push the result to Feishu.

    Builds via ``_prepare_row_transition`` (rebuild-from-``card_state`` preferred;
    legacy single-row splice as fallback — see that function), then sends it.
    ``edit_card_impl`` syncs the Channel's own click-time snapshot to this new content
    (see its docstring) — without that sync, every click after this one would find a
    stale snapshot, fail its check, and degrade into a slow fetch-and-fallback
    ("已提交" placeholder) instead of a fast, in-place update. Returns a short status
    string (``"ok"``, or a ``"skipped_..."``/``"edit_failed: ..."`` reason) — never raises.
    """
    status, new_card = _prepare_row_transition(
        payload=payload,
        value=value,
        index=index,
        new_done=new_done,
        new_round=new_round,
        fallback_title=fallback_title,
        fallback_task_guid=fallback_task_guid,
    )
    if new_card is None:
        return status
    edit_result = await _f.edit_card_impl(message_id, json.dumps(new_card, ensure_ascii=False), user_key)
    return "ok" if edit_result.get("ok") else f"edit_failed: {edit_result}"


async def _sync_ledger_status(value: dict[str, Any], status: str, user_key: str = "") -> None:
    """Best-effort: write the mentor ledger's 状态 field for this row, if it has one.

    Silently does nothing when the row was never wired to a ledger record (no
    ``ledger_record_id``/``ledger_app_token``/``ledger_table_id`` resolvable from the
    click) — that is the normal case for a card sent without ``ledger_app_token``/
    ``ledger_table_id`` in ``feishu_todo_card_send``. Checks ``value`` directly first
    (legacy per-field shape), then falls back to the row's entry inside ``card_state``
    (new shape, where ``ledger_record_id`` lives on the row and the app/table id live at
    the card level).

    The write goes out with ``prefer="user"`` under the clicker's ``user_key``: the bot
    is typically **not** a collaborator on the ledger base (``feishu_mentor_ledger_ensure``
    cannot grant the app itself — see ``bot_access: "not_granted"``), so a tenant-token
    write would fail silently. The clicker owns the rows and has edit rights, which is
    exactly the identity this sync should use.
    """
    record_id = str(value.get("ledger_record_id") or "").strip()
    app_token = str(value.get("ledger_app_token") or "").strip()
    table_id = str(value.get("ledger_table_id") or "").strip()
    if not (record_id and app_token and table_id):
        state_json = value.get("card_state")
        if isinstance(state_json, str) and state_json.strip():
            state = _parse_card_state(state_json)
            if state is not None:
                app_token = app_token or str(state.get("ledger_app_token") or "").strip()
                table_id = table_id or str(state.get("ledger_table_id") or "").strip()
                if not record_id:
                    index_raw = value.get("todo_index")
                    rows = state.get("rows")
                    if isinstance(index_raw, int) and isinstance(rows, list) and 0 <= index_raw < len(rows):
                        row = rows[index_raw]
                        if isinstance(row, dict):
                            record_id = str(row.get("ledger_record_id") or "").strip()
    if not (record_id and app_token and table_id):
        return
    records_json = json.dumps([{"record_id": record_id, "fields": {"状态": status}}], ensure_ascii=False)
    await _f.update_bitable_records_impl(app_token, table_id, records_json, user_key=user_key)
