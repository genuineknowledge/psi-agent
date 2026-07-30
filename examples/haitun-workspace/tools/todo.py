"""Manage the current session task list (plan-then-execute for multi-step work)."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _todo_store as _store


async def todo(
    todos: list[dict[str, str]] | None = None,
    merge: bool = False,
    workspace: str = "",
) -> str:
    """Manage your task list for the current session.

    **When to use** (authoritative): ``skills/task-planning/SKILL.md``.
    Create a list only when multi-step work is worth tracking (≈3+ checkable
    steps, multi-file/multi-deliverable, or a long tool chain). Skip for
    one-shot Q&A, pure chat, or when the user wants a direct result. Do **not**
    invent a decorative list for the UI.

    The agent decides — users need not say "break this down".

    **Read:** empty ``todos`` (default).

    **Write:** ``todos`` as a JSON array string, e.g.
    ``[{"id":"1","content":"…","status":"in_progress"}]``.
    Each ``content`` must be a **string** (not a list/object).

    - ``merge=false`` (default): replace the entire list.
    - ``merge=true``: update by ``id``, append new ids.

    Status: ``pending`` | ``in_progress`` | ``completed`` | ``cancelled``.
    Order is priority. Only **one** ``in_progress`` at a time. Mark
    ``completed`` as soon as a step finishes; on failure ``cancelled`` + add a
    revised item with ``merge=true``.

    Do **not** put self-referential steps in ``content`` (e.g.「更新清单」「回复
    用户」)—writes still succeed but return ``warnings[]`` (soft advisory).

    Always returns the full list and summary counts.

    Args:
        todos: JSON array of task items, or empty to read the current list.
        merge: When true, merge by id; when false, replace the whole list.
        workspace: Workspace root. Empty = current workspace.

    Returns:
        JSON with ok, session_id, todos[], summary{{total, pending, …}}, …
    """
    if todos is None:
        result = await _store.read_todos(workspace_raw=workspace)
    else:
        parsed: Any = todos
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                result = {"ok": False, "message": f"todos must be a JSON array: {exc}"}
                return json.dumps(result, ensure_ascii=False)
        if not isinstance(parsed, list):
            result = {"ok": False, "message": "todos must be a JSON array"}
        else:
            result = await _store.write_todos(todos=parsed, merge=merge, workspace_raw=workspace)
    return json.dumps(result, ensure_ascii=False)
