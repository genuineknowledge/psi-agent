"""Feishu/Lark contact (通讯录) tools — read the org chart and administer it.

What is left here are the reads that do more than forward one request: a
department's roster (``feishu_department_members``), someone by phone or email
(``feishu_contact_find``), the org chart (``feishu_department_tree`` /
``feishu_department_get``), and a user group's members
(``feishu_user_group_members``).

Everything else in this domain — searching the org by name, creating/modifying
users and departments, marking people as resigned, and managing user groups — is
now an endpoint table rather than a tool: call ``feishu_api`` and read the
``feishu-contact`` skill first. The irreversible ones (resign a user, delete a
department or user group) are gated there by an explicit ``confirm`` phrase.

Those write endpoints only accept the app's own tenant token and need the
``contact:contact`` scope (``contact:group`` for user groups) — authorizing as a
user does not help. Their most common failure is not a bad parameter but the app's
**通讯录权限范围** (set in the developer console) not covering the target.

Requires the app's 通讯录权限范围 to cover the members you want to see, the
``contact:contact.base:readonly`` scope (plus ``contact:user.employee_id:readonly``
for the ``user_id``/employee-id field). Reading ``mobile`` / ``email`` also needs
``contact:user.phone:readonly`` / ``contact:user.email:readonly`` (empty otherwise);
looking someone up *by* phone/email needs ``contact:user.id:readonly``.
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


async def feishu_contact_find(
    mobiles: str = "",
    emails: str = "",
    include_resigned: bool = True,
    user_id_type: str = "open_id",
) -> str:
    """Find users by phone number or email — exact match, returns their open_id and name.

    Use this when you have someone's contact details but not their id: a phone number
    from a form, an email from a ticket. Searching by *name* (``GET
    /open-apis/search/v1/user`` via ``feishu_api``) needs the user to have authorized;
    this matches phone/email exactly and works with the bot's own token.

    Returns ``users[]`` with ``{user_id, matched_by, matched_value, name, job_title,
    department_ids, is_resigned, is_activated}``, plus ``not_found[]`` listing which
    inputs matched nobody — so "we couldn't find them" is distinguishable from "we
    didn't ask about them".

    Three things make a lookup come back empty even though the person exists:
    **enterprise emails are not supported** (only personal ones), non-mainland-China
    phone numbers must carry a ``+`` country code, and the app's 通讯录权限范围 must
    cover the user. Resigned employees are included by default here (Feishu's own
    default omits them silently, which makes "resigned" look identical to "no such
    person") — pass ``include_resigned=False`` to exclude them.

    Args:
        mobiles: Phone numbers, comma-separated (max 50). Non-mainland numbers need a
            leading ``+`` and country code, e.g. ``+819012345678``.
        emails: Email addresses, comma-separated (max 50). Personal addresses only —
            enterprise mailboxes are never matched by this API.
        include_resigned: Also match employees who have left. Default True.
        user_id_type: Id form to return — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.find_users_by_contact_impl(mobiles, emails, include_resigned, user_id_type))


async def feishu_department_tree(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    max_depth: int = 2,
    include_member_count: bool = True,
) -> str:
    """List the sub-departments under a department as a nested org chart.

    Start from ``"0"`` (the organization root) to see the whole company structure, or
    from a department id to see one branch. Each node carries ``{department_id, name,
    parent_department_id, leader_user_id, primary_leader_ids, deputy_leader_ids,
    member_count, primary_member_count, chat_id}`` and a ``children`` list.

    ``member_count`` includes everyone in the sub-tree; ``primary_member_count`` counts
    only people whose *primary* department is this one — both are returned because
    "how many people are in this department" has two legitimate answers.

    Use this to get the ``department_id`` that ``feishu_department_members`` and the
    user/department write endpoints (``feishu_api``, see the ``feishu-contact`` skill)
    need. For one department's full detail plus its path from the root, use
    ``feishu_department_get``.

    Depth is walked one level at a time and capped by ``max_depth``, so an org too
    large to fetch at once comes back truncated (with ``truncated: true``) rather than
    failing outright. An empty result with the bot's own token usually means the app's
    通讯录权限范围 isn't set to 全部成员 — Feishu returns nothing rather than an error.

    Args:
        department_id: Where to start; "0" is the organization root (default).
        department_id_type: Id form — open_department_id (default) or department_id.
        user_id_type: Id form for leader ids — open_id (default), union_id, user_id.
        max_depth: How many levels to expand, 1-10. 1 = direct children only. Default 2.
        include_member_count: Include the member-count fields. Default True.
    """
    return _f.dumps_result(
        await _f.department_tree_impl(department_id, department_id_type, user_id_type, max_depth, include_member_count)
    )


async def feishu_department_get(
    department_id: str,
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    include_children: bool = True,
    include_path: bool = True,
    user_key: str = "",
) -> str:
    """Get one department's full detail — leaders, member counts, and where it sits.

    Returns ``department`` (name, parent, ``leader_user_id``, ``primary_leader_ids`` /
    ``deputy_leader_ids`` split out of Feishu's combined ``leaders`` array,
    ``department_hrbps``, ``chat_id``, ``member_count``, ``primary_member_count``),
    its direct ``children``, and ``ancestors`` plus a readable ``path_text`` like
    ``"公司/研发中心/平台组"`` — the answer to "where in the org chart is this".

    Use it to confirm you have the right department before a write, and to find the
    person in charge (``primary_leader_ids``) when you need an approver or owner.
    The root department ``"0"`` has no detail to fetch — list from it with
    ``feishu_department_tree`` instead.

    Args:
        department_id: The department id (not "0"). Get one from ``feishu_department_tree``.
        department_id_type: Id form — open_department_id (default) or department_id.
        user_id_type: Id form for leader/HRBP ids — open_id (default), union_id, user_id.
        include_children: Also list direct sub-departments. Default True.
        include_path: Also resolve the path from the root (ancestors + path_text). Default True.
        user_key: The message sender's open_id, so a department the bot cannot see can
            still be read under that user's own visibility. Optional.
    """
    return _f.dumps_result(
        await _f.department_get_impl(
            department_id, department_id_type, user_id_type, include_children, include_path, user_key
        )
    )


async def feishu_user_group_members(
    group_id: str,
    action: str = "list",
    user_ids: str = "",
    member_id_type: str = "open_id",
    member_type: str = "user",
    page_size: int = 50,
    page_token: str = "",
) -> str:
    """List, add, or remove the members of a user group.

    ``action="list"`` returns the roster. Feishu returns **one member category per
    call** — pass ``member_type="department"`` to see department members, which are
    reported separately from users rather than mixed in.

    ``action="add"`` / ``"remove"`` take a comma-separated ``user_ids``. Feishu's API
    only accepts one member at a time, so these loop and report each person's outcome:
    ``succeeded[]`` plus ``failed[]`` with a per-person reason, and ``partial: true``
    when some worked. Adding someone already in the group is reported as 42005 rather
    than treated as an error worth stopping for; resigned employees cannot be added
    (42006). Only ``member_type="user"`` can be added or removed — Feishu does not yet
    support department members here.

    The most common failure is a mismatch: ``member_id_type`` must describe the ids you
    actually pass (41072 means it doesn't). Needs the ``contact:group`` scope
    (``contact:group:readonly`` to list).

    Args:
        group_id: The user group id, from ``GET /open-apis/contact/v3/group/simplelist``.
        action: list (default) | add | remove.
        user_ids: Comma-separated member ids for add/remove, in the form given by member_id_type.
        member_id_type: Id form of user_ids — open_id (default), union_id, or user_id.
        member_type: user (default) or department. Only "user" works for add/remove.
        page_size: Members per page for list, 1-100. Default 50.
        page_token: Pagination cursor from a previous list call.
    """
    return _f.dumps_result(
        await _f.user_group_members_impl(group_id, action, user_ids, member_id_type, member_type, page_size, page_token)
    )
