"""Feishu/Lark task (任务) tools — create/assign, list, update, complete.

Manage Feishu native tasks: assign work to people with a due date, list tasks,
update them, and mark them done. Good for distributing and tracking work — the
task shows up in each assignee's Feishu Tasks with reminders and a done state.

To assign to someone, resolve their open_id first (e.g. via
``feishu_chat_find_member``). Requires ``PSI_FEISHU_APP_ID`` /
``PSI_FEISHU_APP_SECRET`` and the ``task:task:write`` scope. Same-tenant members
only when using the bot's credentials.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_task_create(
    summary: str, description: str = "", due: str = "", assignees: str = "", followers: str = "", user_key: str = ""
) -> str:
    """Create a Feishu task, optionally assigning people and a due date.

    Returns the new task's ``task_guid`` and ``url``.

    Args:
        summary: Task title (required).
        description: Optional longer description.
        due: Optional due date — 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'.
        assignees: Comma-separated open_ids of people responsible (assignees).
        followers: Comma-separated open_ids of followers (kept in the loop).
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to create
            the task as that user (owned by them); empty uses the bot's tenant token.
    """
    return _f.dumps_result(await _f.create_task_impl(summary, description, due, assignees, followers, user_key))


async def feishu_task_get(task_guid: str) -> str:
    """Get a task's detail, including whether it's completed and who completed it.

    Use this to check completion of a task assigned to someone else: create/assign
    the task (keep its ``task_guid``), then query here. Returns ``status`` (todo/done),
    ``completed`` (bool), ``completed_at``, the ``members``, and per-assignee
    ``assignee_completion``. Works for any task the bot can read (e.g. one it created).

    Args:
        task_guid: The task's guid (from ``feishu_task_create`` or ``feishu_task_list``).
    """
    return _f.dumps_result(await _f.get_task_impl(task_guid))


async def feishu_task_list(completed: str = "", page_size: int = 50, page_token: str = "") -> str:
    """List the bot's own tasks (tasks the calling identity is responsible for).

    Note: this lists tasks assigned to the bot itself, not an arbitrary person's
    tasks (Feishu's API only exposes the caller's own "my_tasks").

    Args:
        completed: '' for all, 'true' for completed only, 'false' for open only.
        page_size: Max tasks per page (1-100, default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.list_tasks_impl(completed, page_size, page_token))


async def feishu_task_update(
    task_guid: str, summary: str = "", description: str = "", due: str = "", user_key: str = ""
) -> str:
    """Update a task's summary, description, and/or due date (only the fields you pass).

    Args:
        task_guid: The task's guid (from ``feishu_task_create`` or ``feishu_task_list``).
        summary: New title (omit to leave unchanged).
        description: New description (omit to leave unchanged).
        due: New due date 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' (omit to leave unchanged).
        user_key: The sender's open_id; pass it to act as that user (see feishu_task_create).
    """
    return _f.dumps_result(await _f.update_task_impl(task_guid, summary, description, due, user_key))


async def feishu_task_complete(task_guid: str, completed: bool = True, user_key: str = "") -> str:
    """Mark a task complete, or reopen it.

    Args:
        task_guid: The task's guid.
        completed: True to complete (default), False to reopen an already-completed task.
        user_key: The sender's open_id; pass it to act as that user (see feishu_task_create).
    """
    return _f.dumps_result(await _f.complete_task_impl(task_guid, completed, user_key))
