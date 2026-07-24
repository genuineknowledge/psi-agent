"""Feishu/Lark approval (审批) tools — submit applications and read/approve them.

Covers both ends of an approval:
- **发起端 (submit)** — ``feishu_approval_get_definition`` reads what fields an
  approval requires (its form template) and ``feishu_approval_create`` submits an
  instance *on behalf of an applicant* (records under the applicant's open_id).
- **审核端 (audit)** — list a user's tasks, read an application's form, and
  approve/reject on behalf of a real approver.

Important: approve/reject carries the **approver's own user_id** (the bot acts on
behalf of a real approver). Create carries the **applicant's open_id/user_id** so
the instance is recorded under that person; the bot's tenant token submits it, so
no per-applicant authorization is needed. Requires the app authorized on the
approval definition and the ``approval:*`` scopes, plus ``PSI_FEISHU_APP_ID`` /
``PSI_FEISHU_APP_SECRET``.
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


async def feishu_approval_list_instances(approval_code: str, start_time: str = "", end_time: str = "") -> str:
    """List every approval instance code for one approval definition in a time window.

    Use this to enumerate all applications of a given approval (e.g. every reimbursement)
    so you can read each one with ``feishu_approval_get``. Feeds reimbursement/attendance
    report flows.

    Args:
        approval_code: The approval definition code (identifies which approval flow).
        start_time: Window start as a Unix millisecond timestamp string (optional; defaults to 30 days ago).
        end_time: Window end as a Unix millisecond timestamp string (optional; defaults to now).
    """
    return _f.dumps_result(await _f.list_approval_instances_impl(approval_code, start_time, end_time))


async def feishu_approval_get(instance_id: str, user_id_type: str = "open_id") -> str:
    """Read an approval instance's detail — applicant, status, submitted form, and task_list.

    Use this to inspect what an application actually contains before deciding.
    The ``form`` field is a JSON string of the submitted form widgets, and
    ``attachments`` lists downloadable files pulled from that form: each is
    ``{name, type, kind, value}`` where kind ``"url"`` is a direct link (valid
    only ~12h — download promptly with ``feishu_file_download`` is_url=True) and
    kind ``"drive"`` is a media token (download with is_url=False).

    Args:
        instance_id: The approval instance code (from list_tasks or list_instances).
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


async def feishu_approval_get_definition(
    approval_code: str, user_id_type: str = "open_id", with_admin_id: bool = False
) -> str:
    """Read an approval definition's form template so you know which fields to fill before submitting.

    Returns the ``form`` as a widget list — each ``{id, custom_id, name, type, required}`` —
    plus a ``node_list`` summary of the approval chain. Feed each widget's ``id`` and
    ``type`` into ``feishu_approval_create``'s form_json. Read this first and map the
    applicant's words onto the real field ids/types — never invent field ids.

    Args:
        approval_code: The approval definition code (identifies which approval flow).
        user_id_type: Id form for returned user ids — open_id (default), union_id, user_id.
        with_admin_id: True to also return the definition's admin user ids (optional).
    """
    return _f.dumps_result(await _f.get_approval_definition_impl(approval_code, user_id_type, with_admin_id))


async def feishu_approval_create(
    approval_code: str,
    form_json: str,
    applicant_open_id: str = "",
    applicant_user_id: str = "",
    node_approver_open_id_list_json: str = "",
    title: str = "",
    user_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Submit an approval application on behalf of an applicant. Returns the new instance_code.

    Use this to file a leave/reimbursement/etc. application for someone. The
    instance is recorded under the applicant, so pass the requester's own id —
    in a Feishu DM that is the ``sender_open_id`` from ``<feishu_context>``.
    Build ``form_json`` from ``feishu_approval_get_definition`` first, and confirm
    the filled form with the applicant before submitting (see the
    feishu-self-service-agent skill / admin-finance-governance).

    Args:
        approval_code: The approval definition code (which approval flow to file).
        form_json: JSON array of ``{"id","type","value"}`` widgets, ids/types from get_definition.
        applicant_open_id: The applicant's open_id (the DM sender's open_id). Pass this or applicant_user_id.
        applicant_user_id: The applicant's user_id (alternative to applicant_open_id).
        node_approver_open_id_list_json: Optional JSON array of ``{"key":node_id,"value":[open_id,...]}``
            for flows where the initiator picks approvers.
        title: Optional custom instance title.
        user_id_type: Id form for the ids above — open_id (default), union_id, user_id.
        user_key: Optional UAT slot (a user's open_id) to submit as that user; empty uses the bot's tenant token.
    """
    return _f.dumps_result(
        await _f.create_approval_instance_impl(
            approval_code,
            form_json,
            applicant_open_id,
            applicant_user_id,
            node_approver_open_id_list_json,
            title,
            user_id_type,
            user_key,
        )
    )
