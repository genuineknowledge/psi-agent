"""Feishu/Lark contact (通讯录) tools — read the org chart and administer it.

Read side: get a department's roster, resolve a person's contact details by id,
search the whole org by name (``feishu_contact_search``), find someone by phone or
email (``feishu_contact_find``), and walk the org chart
(``feishu_department_tree`` / ``feishu_department_get``).

Write side (``feishu_user_manage``, ``feishu_department_manage``,
``feishu_user_group``, ``feishu_user_group_members``): create/modify users and
departments, mark people as resigned, and manage user groups. These change the
organization for everyone, so the irreversible ones (resign a user, delete a
department or user group) require an explicit ``confirm`` phrase.

The write endpoints only accept the app's own tenant token and need the
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


async def feishu_contact_find(
    mobiles: str = "",
    emails: str = "",
    include_resigned: bool = True,
    user_id_type: str = "open_id",
) -> str:
    """Find users by phone number or email — exact match, returns their open_id and name.

    Use this when you have someone's contact details but not their id: a phone number
    from a form, an email from a ticket. ``feishu_contact_search`` matches *names* and
    needs the user to have authorized; this matches phone/email exactly and works with
    the bot's own token.

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

    Use this to get the ``department_id`` that ``feishu_department_members``,
    ``feishu_user_manage`` and ``feishu_department_manage`` need. For one department's
    full detail plus its path from the root, use ``feishu_department_get``.

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


async def feishu_user_manage(
    action: str,
    user_id: str = "",
    name: str = "",
    mobile: str = "",
    department_ids: str = "",
    employee_type: int = 0,
    email: str = "",
    leader_user_id: str = "",
    job_title: str = "",
    employee_no: str = "",
    en_name: str = "",
    nickname: str = "",
    gender: int = 0,
    city: str = "",
    work_station: str = "",
    enterprise_email: str = "",
    confirm: str = "",
    docs_acceptor_user_id: str = "",
    calendar_acceptor_user_id: str = "",
    application_acceptor_user_id: str = "",
    department_chat_acceptor_user_id: str = "",
    external_chat_acceptor_user_id: str = "",
    email_processing_type: str = "",
    email_acceptor_user_id: str = "",
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> str:
    """Create a user, modify their info, or mark them as resigned (onboarding/offboarding).

    ``action="create"`` — Feishu requires ``name``, ``mobile``, ``department_ids`` and
    ``employee_type`` (1 正式 / 2 实习 / 3 外包 / 4 劳务 / 5 顾问). The phone number must
    be unique in the tenant and needs a ``+`` country code if it isn't mainland China.

    ``action="update"`` — only the fields you pass are changed; anything omitted keeps
    its current value. Note ``department_ids`` **replaces** the whole list rather than
    adding to it, so read the user first if you mean to add a department.

    ``action="resign"`` — irreversible offboarding, so it requires
    ``confirm="离职用户"``. Their documents, calendars and apps transfer to their direct
    manager by default; if they have no manager, docs/minutes/apps stay under the
    departed account but **calendars and surveys are deleted outright**. Name the
    ``*_acceptor_user_id`` you want if that matters. Tenant admins cannot be resigned
    this way (Feishu returns 44037) — remove their admin role in the console first.

    All three need the ``contact:contact`` scope and the app's own token; the target's
    departments must be inside the app's 通讯录权限范围 or you get 40004 / 41050.

    Args:
        action: create | update | resign.
        user_id: Which user (required for update/resign). Resolve with ``feishu_contact_find``.
        name: Display name. Required for create.
        mobile: Phone number, unique per tenant. Required for create.
        department_ids: Comma-separated department ids (max 50). Required for create;
            on update this REPLACES the user's department list.
        employee_type: 1 正式, 2 实习, 3 外包, 4 劳务, 5 顾问. Required for create.
        email: Personal email, unique per tenant.
        leader_user_id: The user's direct manager (cannot be the user themselves).
        job_title: Job title.
        employee_no: Employee number.
        en_name: English name.
        nickname: Nickname.
        gender: 0 secret, 1 male, 2 female, 3 other. Omit to leave unchanged.
        city: Work city.
        work_station: Desk/seat.
        enterprise_email: Enterprise mailbox address.
        confirm: Must be "离职用户" for action="resign". Ignored otherwise.
        docs_acceptor_user_id: Who inherits their documents on resign.
        calendar_acceptor_user_id: Who inherits their calendars on resign.
        application_acceptor_user_id: Who inherits their apps on resign.
        department_chat_acceptor_user_id: Who takes over department groups they owned.
        external_chat_acceptor_user_id: Who takes over external groups they were in.
        email_processing_type: Mailbox handling on resign — "1" transfer, "2" keep, "3" delete.
        email_acceptor_user_id: Who inherits the mailbox; required when type is "1".
        user_id_type: Id form of user_id / leader ids — open_id (default), union_id, user_id.
        department_id_type: Id form of department_ids — open_department_id (default) or department_id.
    """
    act = (action or "").strip().lower()
    if act == "create":
        return _f.dumps_result(
            await _f.user_create_impl(
                name,
                mobile,
                department_ids,
                employee_type or 1,
                email,
                leader_user_id,
                job_title,
                employee_no,
                en_name,
                nickname,
                gender,
                city,
                work_station,
                enterprise_email,
                user_id_type,
                department_id_type,
            )
        )
    if act == "update":
        return _f.dumps_result(
            await _f.user_update_impl(
                user_id,
                name,
                mobile,
                email,
                department_ids,
                employee_type,
                leader_user_id,
                job_title,
                employee_no,
                en_name,
                nickname,
                gender,
                city,
                work_station,
                enterprise_email,
                user_id_type,
                department_id_type,
            )
        )
    if act == "resign":
        return _f.dumps_result(
            await _f.user_resign_impl(
                user_id,
                confirm,
                docs_acceptor_user_id,
                calendar_acceptor_user_id,
                application_acceptor_user_id,
                department_chat_acceptor_user_id,
                external_chat_acceptor_user_id,
                email_processing_type,
                email_acceptor_user_id,
                user_id_type,
            )
        )
    return _f.dumps_result(_f.error_result(f"action 只能是 create, update, resign (当前 '{action}')。"))


async def feishu_department_manage(
    action: str,
    department_id: str = "",
    name: str = "",
    parent_department_id: str = "",
    leader_user_id: str = "",
    order: str = "",
    custom_department_id: str = "",
    create_group_chat: bool = False,
    confirm: str = "",
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> str:
    """Create, modify, move, or delete a department (org-structure changes).

    ``action="create"`` — needs ``name`` and ``parent_department_id`` ("0" puts it at
    the top level). Names cannot contain ``/`` and must be unique under the same parent.

    ``action="update"`` — only the fields you pass change. Passing
    ``parent_department_id`` **moves** the department (and everything under it) to a new
    parent; ``"0"`` moves it to the top level. Setting ``leader_user_id`` also updates
    the primary leader, since Feishu keeps the two in sync.

    ``action="delete"`` — irreversible, so it requires ``confirm="删除部门"``. Feishu
    also demands the department be **empty first**: still has people → 43011, still has
    sub-departments → 43012. Deleting a sub-tree therefore has to go deepest-first.

    Needs the ``contact:contact`` scope and the app's own token, and the department (and
    its parent) must be inside the app's 通讯录权限范围.

    Args:
        action: create | update | delete.
        department_id: Which department (required for update/delete). Never "0" — the
            root cannot be modified or deleted.
        name: Department name. Required for create; no "/" allowed.
        parent_department_id: Parent id; "0" is the top level. Required for create.
            On update, passing this MOVES the department.
        leader_user_id: The department head.
        order: Sort weight among siblings; must be unique under the same parent.
        custom_department_id: Your own id for the department (create only). Cannot start
            with "od-" and cannot be "0" or "1".
        create_group_chat: Also create the department's group chat (create only). Default False.
        confirm: Must be "删除部门" for action="delete". Ignored otherwise.
        user_id_type: Id form of leader_user_id — open_id (default), union_id, user_id.
        department_id_type: Id form of the department ids — open_department_id (default)
            or department_id.
    """
    act = (action or "").strip().lower()
    if act == "create":
        return _f.dumps_result(
            await _f.department_create_impl(
                name,
                parent_department_id,
                leader_user_id,
                custom_department_id,
                order,
                create_group_chat,
                user_id_type,
                department_id_type,
            )
        )
    if act == "update":
        return _f.dumps_result(
            await _f.department_update_impl(
                department_id,
                name,
                parent_department_id,
                leader_user_id,
                order,
                user_id_type,
                department_id_type,
            )
        )
    if act == "delete":
        return _f.dumps_result(await _f.department_delete_impl(department_id, confirm, department_id_type))
    return _f.dumps_result(_f.error_result(f"action 只能是 create, update, delete (当前 '{action}')。"))


async def feishu_user_group(
    action: str,
    group_id: str = "",
    name: str = "",
    description: str = "",
    custom_group_id: str = "",
    group_type: int = 1,
    confirm: str = "",
    page_size: int = 50,
    page_token: str = "",
) -> str:
    """Manage user groups (用户组) — named sets of people usable as a permission subject.

    A user group is not a chat: you cannot message it, but you *can* grant it access to
    a document or reference it in an approval flow. Use ``feishu_chat_create`` when you
    want somewhere to talk.

    ``action="list"`` gets the groups (each with ``group_id``, name, member counts),
    ``"get"`` one group's detail, ``"create"`` makes one (``name`` required, unique in
    the tenant), ``"update"`` changes name/description, and ``"delete"`` removes it —
    which requires ``confirm="删除用户组"`` because anything that referenced the group
    for permissions loses that subject.

    Creating a group requires the app's 通讯录权限范围 to be **全部成员** (otherwise
    42010) even though the other actions don't. Dynamic groups (``group_type=2``) can be
    listed but not created through the API — those are console-only. Needs the
    ``contact:group`` scope (``contact:group:readonly`` for list/get).

    Use ``feishu_user_group_members`` to put people in a group.

    Args:
        action: create | list | get | update | delete.
        group_id: Which group (required for get/update/delete).
        name: Group name — required for create, unique per tenant, max 100 chars.
        description: Group description, max 500 chars.
        custom_group_id: Your own id for the group (create only), letters and digits.
        group_type: 1 普通用户组 (default), 2 动态用户组. Applies to create and list.
        confirm: Must be "删除用户组" for action="delete". Ignored otherwise.
        page_size: Groups per page for list, 1-100. Default 50.
        page_token: Pagination cursor from a previous list call.
    """
    return _f.dumps_result(
        await _f.user_group_manage_impl(
            action, group_id, name, description, custom_group_id, group_type, confirm, page_size, page_token
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
        group_id: The user group id, from ``feishu_user_group(action='list')``.
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
