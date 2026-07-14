"""Feishu/Lark approval (审批) tools — read applications and approve/reject them.

Let the agent list a user's pending approval tasks, read an application's form
content, and decide whether to approve or reject.

Important: Feishu requires approve/reject to carry the **approver's own user_id**,
so the bot acts *on behalf of* a real approver — the action is recorded under
that person. Requires the app to be authorized on the approval definition and
the ``approval:*`` scopes, plus ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_approval_list_tasks(
    user_id: str,
    topic: str = "1",
    user_id_type: str = "open_id",
    page_size: int = 100,
    page_token: str = "",
) -> str:
    """List a user's approval tasks (e.g. their pending approvals).

    Returns task summaries — each with ``task_id``, ``instance_code``,
    ``approval_code``, ``title``, ``status`` — which you feed into
    ``feishu_approval_get`` (to read the form) and ``feishu_approval_decide``.

    Args:
        user_id: The user whose tasks to list (id form matches user_id_type).
        topic: Task group — "1" pending/待办 (default), "2" done, "3" initiated, "17"/"18" cc.
        user_id_type: Id form for user_id and returned ids — open_id (default), union_id, user_id.
        page_size: Max tasks per page (default 100, max 200).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.list_approval_tasks_impl(user_id, topic, user_id_type, page_size, page_token))


async def feishu_approval_get(instance_id: str, user_id_type: str = "open_id") -> str:
    """Read an approval instance's detail — applicant, status, submitted form, and task_list.

    Use this to inspect what an application actually contains before deciding.
    The ``form`` field is a JSON string of the submitted form widgets.

    Args:
        instance_id: The approval instance code (from ``feishu_approval_list_tasks``).
        user_id_type: Id form for returned user ids — open_id (default), union_id, user_id.
    """
    return _f.dumps_result(await _f.get_approval_instance_impl(instance_id, user_id_type))


async def feishu_approval_decide(
    approve: bool,
    approval_code: str,
    instance_code: str,
    approver_user_id: str,
    task_id: str,
    comment: str = "",
    user_id_type: str = "open_id",
) -> str:
    """Approve or reject an approval task on behalf of an approver.

    ``approve=True`` approves, ``approve=False`` rejects. Feishu records the action
    under ``approver_user_id`` — this must be the real approver's user id, and that
    person must be the current task's assignee.

    Args:
        approve: True to approve, False to reject.
        approval_code: The approval definition code (from the task/instance).
        instance_code: The approval instance code.
        approver_user_id: The approver's user id (id form matches user_id_type).
        task_id: The approval task id (from the instance's task_list or the task list).
        comment: Optional approval/rejection comment.
        user_id_type: Id form for approver_user_id — open_id (default), union_id, user_id.
    """
    return _f.dumps_result(
        await _f.decide_approval_task_impl(
            approve, approval_code, instance_code, approver_user_id, task_id, comment, user_id_type
        )
    )
