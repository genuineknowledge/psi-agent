"""Feishu/Lark contact (通讯录) tools — list department members.

Get the roster (user ids + names) for a department, or the whole organization
from the root department "0". This gives the agent the ``user_id`` list needed to
batch-query attendance and compute payroll.

Also resolve a person's contact details (mobile / email / job title) by id, so an
employee stuck on a blocker can be handed the right owner's way to reach them.

Or search the whole org for a user by name (``feishu_contact_search``) when you
don't already know which group or department they're in — this needs a
user_access_token (authorize once via ``feishu_auth_start``).

Requires the app's 通讯录权限范围 to cover the members you want to see, the
``contact:contact.base:readonly`` scope (plus ``contact:user.employee_id:readonly``
for the ``user_id``/employee-id field). Reading ``mobile`` / ``email`` also needs
``contact:user.phone:readonly`` / ``contact:user.email:readonly`` (empty otherwise).
Set ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_department_members(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    recursive: bool = False,
) -> str:
    """List the members of a department (or the whole org from root "0").

    Returns de-duplicated members, each ``{user_id, open_id, name}``. Use the ids
    to batch-query attendance (``feishu_attendance_query``) or compute payroll.

    Args:
        department_id: Department id ("0" is the organization root). Default "0".
        department_id_type: Id form for department_id — open_department_id (default) or department_id.
        user_id_type: Id form for returned member ids — open_id (default), union_id, user_id.
        recursive: If True, also include members of all sub-departments. Default False.
    """
    return _f.dumps_result(
        await _f.list_department_members_impl(department_id, department_id_type, user_id_type, recursive)
    )


async def feishu_user_get(
    user_ids: str,
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> str:
    """Get colleagues' contact details (mobile, email, job title) by id, up to 50 at once.

    Use this to hand someone a way to reach the right person — e.g. after finding who
    owns a blocked area, look up that owner's phone/email here so you can share it.
    Returns per user ``{open_id, user_id, name, mobile, email, enterprise_email,
    job_title, department_ids, leader_user_id}``. ``mobile``/``email`` come back empty
    unless the app has the phone/email contact scopes and the 通讯录权限范围 covers them.

    Args:
        user_ids: Comma-separated ids (max 50), in the form given by user_id_type.
        user_id_type: Id form of user_ids — open_id (default), union_id, or user_id.
        department_id_type: Id form for returned department_ids — open_department_id (default) or department_id.
    """
    return _f.dumps_result(await _f.get_users_batch_impl(user_ids, user_id_type, department_id_type))


async def feishu_contact_search(query: str, page_size: int = 20, page_token: str = "", user_key: str = "") -> str:
    """Search the whole org for users by name — no group or department needed.

    This is the way to resolve a person by name when you *don't* know where they
    are. ``feishu_chat_find_member`` only searches a group you know the ``chat_id``
    of, and ``feishu_department_members`` needs a department id; this matches any
    user across the organization by a name keyword. Use it to turn a bare name into
    an ``open_id`` before @-mentioning, direct-messaging, or looking up contact
    details with ``feishu_user_get``.

    Returns matches as ``{open_id, user_id, name, avatar, department_ids}`` plus a
    ``count``, ``has_more`` and ``page_token`` for the next page. If several people
    share the name, all are returned — pick the right ``open_id``.

    Feishu only allows this search with a user_access_token (the bot's own token
    can't call it), so the caller must have authorized once — an unauthorized call
    returns ``need_auth`` (see ``feishu_auth_start`` / ``feishu_auth_complete``).

    Args:
        query: Name (or name keyword) to search for across the organization.
        page_size: Max users to return per page (1-200, default 20).
        page_token: Pagination cursor from a previous call's ``page_token`` (optional).
        user_key: The message sender's open_id (from ``<feishu_context>``), so the
            search runs as that authorized user. Must match the ``user_key`` used
            when authorizing. Empty uses the shared ``default`` slot.
    """
    return _f.dumps_result(await _f.search_users_impl(query, page_size, page_token, user_key))
