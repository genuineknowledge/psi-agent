"""A TODO-list card: many rows, each tickable on its own, each linked to a Feishu task.

Distinct from ``feishu_message_send_card`` (one card, one answer) because a todo list is
consumed row by row. The single-use machinery still applies **per row**, so ticking one
row cannot be replayed while the other rows stay live.
"""

from __future__ import annotations

import json
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
_ACTION_PREFIX = "todo_tick"
_MAX_ITEMS = 40


def _shape_chars(shape: str) -> tuple[str, str]:
    return _SHAPES.get(shape.strip().casefold() or _DEFAULT_SHAPE, _SHAPES[_DEFAULT_SHAPE])


def _task_url(task_guid: str, link: str) -> str:
    """The clickable target for a row: an explicit link wins, else the task applink."""
    if link.strip():
        return link.strip()
    if task_guid.strip():
        # applink 是飞书官方的客户端跳转协议, 不依赖任务接口返回 web url
        # (task/v2 的返回体里没有 url 字段, 别去等它)。
        return f"https://applink.feishu.cn/client/todo/detail?guid={task_guid.strip()}"
    return ""


def _row_elements(item: dict[str, Any], index: int, shape_override: str) -> list[dict[str, Any]]:
    title = str(item.get("title") or "").strip() or f"任务 {index + 1}"
    done = bool(item.get("done"))
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
    if not done:
        # 每行一个独立 action_id: 多选消费就是按它落墓碑的, 撞名会让两行互相顶掉。
        action_id = f"{_ACTION_PREFIX}_{index}"
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"{empty} 标记完成"},
                        "type": "default",
                        "value": {
                            "action": action_id,
                            "todo_index": index,
                            "todo_title": title,
                            "task_guid": task_guid,
                        },
                    }
                ],
            }
        )
    return elements


def _build_todo_card(
    *,
    items: list[dict[str, Any]],
    title: str,
    subtitle: str,
    shape: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    done_count = sum(1 for item in items if item.get("done"))
    header_lines = [f"进度: {done_count}/{len(items)} 已完成"]
    if subtitle.strip():
        header_lines.insert(0, subtitle.strip())

    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n".join(header_lines)}]
    handlers: dict[str, str] = {}
    for index, item in enumerate(items):
        elements.append({"tag": "hr"})
        elements.extend(_row_elements(item, index, shape))
        if not item.get("done"):
            handlers[f"{_ACTION_PREFIX}_{index}"] = "feishu_todo_card_tick"

    card = {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title.strip() or "今日 TODO"},
            "template": "green" if done_count == len(items) and items else "blue",
        },
        "elements": elements,
    }
    return card, handlers


async def feishu_todo_card_send(
    receive_id: str,
    items_json: str,
    title: str = "今日 TODO",
    subtitle: str = "",
    shape: str = "circle",
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send one card listing a person's todos, each row ticked independently.

    Use this instead of ``feishu_message_send_card`` whenever the recipient must act on
    **several** items from one message (今日待办清单). Each row shows a shape marker, its
    title as a link to the matching Feishu task, an optional detail line, and its own
    「标记完成」button. Ticking a row rewrites that row as ``● ~~已完成~~`` and updates the
    card in place — the other rows keep working, which a normal card cannot do (its first
    click retires the whole card).

    Each row is dispatched to ``feishu_todo_card_tick``, so mark the underlying Feishu task
    complete there. Rows already marked ``done`` are rendered read-only and get no button.

    ``items_json`` is a JSON array (max 40) of objects::

        [{"title": "写周报", "task_guid": "abc-123", "detail": "周五 18:00 前",
          "shape": "square", "done": false, "link": "https://..."}]

    - ``title`` — the todo text (required; a blank one becomes "任务 N").
    - ``task_guid`` — the Feishu task this row links to, from
      ``POST /open-apis/task/v2/tasks``. Rendered as an applink; the task API's response
      carries no web URL, so do not wait for one.
    - ``link`` — an explicit URL that overrides the applink (use it for a doc instead).
    - ``shape`` — per-row shape: circle ○● / square □■ / diamond ◇◆ / triangle △▲ /
      star ☆★ / check ☐☑. Falls back to the card-level ``shape``.
    - ``detail`` — a second line under the title (deadline, acceptance criteria).
    - ``done`` — pre-completed rows render struck-through with no button.

    Create the Feishu tasks **before** calling this so每行都有 ``task_guid``; a row without
    one still ticks, it just is not clickable through to a task.

    Args:
        receive_id: Who gets the card — usually the doer's ``ou_...`` open_id.
        items_json: JSON array of todo objects, described above.
        title: Card header text.
        subtitle: A line above the progress counter (date, mentor, source table).
        shape: Default shape for rows that do not set their own.
        receive_id_type: Auto-detected from the id prefix; only set for a bare user_id.
        user_key: Send as this person instead of the bot. Omit for the bot's own identity.
    """
    if not isinstance(items_json, str):
        return "[Error] items_json must be a JSON string containing an array"
    try:
        raw_items = json.loads(items_json)
    except ValueError as exc:
        return f"[Error] items_json is not valid JSON: {exc}"
    if not isinstance(raw_items, list) or not raw_items:
        return "[Error] items_json must be a non-empty JSON array of todo objects"
    if len(raw_items) > _MAX_ITEMS:
        return f"[Error] too many todos ({len(raw_items)}); split into cards of at most {_MAX_ITEMS}"
    items = [item for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        return "[Error] every item in items_json must be a JSON object"

    card, handlers = _build_todo_card(items=items, title=title, subtitle=subtitle, shape=shape)
    if not handlers:
        return "[Error] every todo is already done; nothing to send"
    result = await _f.send_card_impl(
        receive_id,
        json.dumps(card, ensure_ascii=False),
        receive_id_type,
        user_key or None,
        "{}",
        json.dumps(handlers, ensure_ascii=False),
        True,
    )
    return _f.dumps_result(result)
