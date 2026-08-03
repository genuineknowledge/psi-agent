"""Feishu/Lark chat (group) tools — find a group the bot belongs to by name,
resolve a member's open_id, create a new group (拉人建群), and run it afterwards
(read/change its settings, add/remove members, announcement, menus, tabs).

Use ``feishu_chat_find`` to resolve a human-given group name (e.g. "主群") into a
``chat_id`` before sending messages (the bot must already be a member),
``feishu_chat_list`` when there is no name to search by ("我在哪些群"), or
``feishu_chat_create`` to spin up a brand-new group and pull people into it. Once a
group exists, ``feishu_chat_get`` reads who owns it and how it is configured, and
``feishu_chat_add_members`` / ``feishu_chat_remove_members`` change its roster.

Running the group: ``feishu_chat_announcement`` / ``_set`` / ``_clear`` for 群公告,
``feishu_chat_update`` for name/avatar/permissions, ``feishu_chat_mute`` for 禁言
(a different endpoint from the other settings), ``feishu_chat_menu_*`` and
``feishu_chat_tab*`` for the buttons and tabs, and — irreversibly —
``feishu_chat_transfer_owner`` / ``feishu_chat_dismiss``.

Pair with ``feishu_message`` (send / reply-in-thread / list / search messages).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_chat_find(name: str, exact: bool = False, page_size: int = 50, page_token: str = "") -> str:
    """Find Feishu/Lark groups the bot is in whose name matches ``name``.

    Returns candidate groups as ``{chat_id, name, description}``. If several
    match, all are returned — pick the right ``chat_id`` before sending.

    Args:
        name: Group name (or keyword) to search for.
        exact: When true, keep only groups whose name equals ``name`` exactly.
        page_size: Max groups to return (default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.find_chat_impl(name, exact, page_size, page_token))


async def feishu_chat_find_member(
    chat_id: str, name: str = "", exact: bool = False, member_id_type: str = "open_id"
) -> str:
    """Resolve a group member's user id (open_id) by their name.

    Feishu bots can't search all users by name, so this lists the group's members
    (each carries a name + id) and matches by name. Use it to turn a person's name
    into an ``open_id`` before @-mentioning or direct-messaging them. Pages through
    the full roster automatically.

    Returns matches as ``{name, id, member_id_type}``. If several people share the
    name, all are returned — pick the right ``id``.

    Args:
        chat_id: The group's chat_id (from ``feishu_chat_find``). The person must be a member.
        name: Person's name to match. Empty returns the whole roster.
        exact: When true, match the name exactly; otherwise substring match.
        member_id_type: Id form to return — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.find_member_id_impl(chat_id, name, exact, member_id_type))


async def feishu_chat_list_members(chat_id: str, member_id_type: str = "open_id") -> str:
    """List every member of a Feishu/Lark group in one call.

    Unlike ``feishu_chat_find_member`` (which searches by a specific name), this
    returns the group's whole roster — use it when you need everyone in the group,
    not just a matched person. Pages through the full roster automatically.

    Returns members as ``{name, id, member_id_type}`` plus a total ``count``.

    Args:
        chat_id: The group's chat_id (from ``feishu_chat_find``). The bot must be a member.
        member_id_type: Id form to return — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.list_chat_members_impl(chat_id, member_id_type))


async def feishu_chat_create(
    name: str,
    user_ids: list[str] | None = None,
    description: str = "",
    owner_id: str = "",
    user_id_type: str = "open_id",
) -> str:
    """Create a **new** Feishu/Lark group chat and pull the given people in (拉人建群).

    Use this when there is no existing group to post to — the bot creates the group,
    hands it to the **requester** as owner (``owner_id``), and stays on as an admin so
    you can still send to the returned ``chat_id`` with ``feishu_message_send``. This is
    the missing piece versus ``feishu_message_send``, which can only post to a group
    that already exists.

    Members are given as user ids, not names: resolve names to open_ids first with
    ``feishu_chat_find_member`` (from another group) or ``feishu_department_members``.
    The response includes the new ``chat_id`` and ``invalid_user_ids`` (ids Feishu
    could not add — e.g. outside the app's contact scope).

    Args:
        name: Group name (required).
        user_ids: Members to invite — a list of ids matching ``user_id_type`` (max 50).
            Empty creates a group with just the bot; invite more later.
        description: Group description/topic (optional).
        owner_id: Id (matching ``user_id_type``) of the person to make group owner.
            **Default to the requester** — pass the ``sender_open_id`` from
            ``<feishu_context>`` so the person who asked for the group owns it (the bot
            stays an admin and can keep posting). Pass someone else's id if the requester
            explicitly wants another person to be owner. Leave empty only for a
            bot-authored group with no human requester (the bot then owns it).
        user_id_type: Id form used by user_ids/owner_id — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.create_chat_impl(name, user_ids, description, owner_id, user_id_type))


async def feishu_chat_get(chat_id: str, user_id_type: str = "open_id", user_key: str = "") -> str:
    """Read a Feishu/Lark group's **details** — owner, member counts, and settings.

    The question this answers before you act on a group: **who owns it** (only the owner
    or an admin may add/remove members or 置顶 in most groups — pass their ``user_key``
    to those tools), **how many people are in it** (a 500-person group is not somewhere
    to send a test message), and **what it allows** (whether the bot can add members
    at all, whether @所有人 is permitted, whether 保密模式 blocks downloads).

    ``settings`` comes back as readable Chinese pairs (e.g. ``{"谁可以加人": "仅群主和管理员"}``)
    rather than Feishu's bare ``only_owner`` enums. ``owner_is_bot`` is true when the
    group is owned by a bot, which is why no ``owner_id`` is returned — not an error.

    Feishu answers a **non-member** caller with only the name, avatar, counts and status;
    that comes back as ``partial=true``. Don't read a thin result as "这个群没有群主/没有
    设置" — add the bot to the group (or pass a member's ``user_key``) and ask again.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        user_id_type: Id form for owner/admin ids — open_id (default), union_id, or user_id.
        user_key: A group member's open_id as a fallback identity, for a group the bot
            isn't in (optional).
    """
    return _f.dumps_result(await _f.get_chat_impl(chat_id, user_id_type, user_key))


async def feishu_chat_add_members(
    chat_id: str,
    user_ids: list[str] | None = None,
    member_id_type: str = "open_id",
    succeed_type: int = 1,
    user_key: str = "",
) -> str:
    """Add people (or bots) to an **existing** Feishu/Lark group (拉人进群).

    The counterpart to ``feishu_chat_create``, which can only pull people in at creation
    time: use this to grow a group that already exists — onboarding a new teammate into
    the project group, pulling a reviewer into a discussion.

    Members are given as ids, not names: resolve them first with
    ``feishu_chat_find_member`` (from another group), ``feishu_contact_search``, or
    ``feishu_department_members``. To add a **bot**, pass its App ID with
    ``member_id_type="app_id"``.

    Partial results are the normal case and are reported separately, because the fix
    differs: ``invalid_ids`` (outside the app's scope, or the person has left),
    ``not_existed_ids`` (no such id), and ``pending_approval_ids`` — those people **will**
    join once the owner approves, so don't re-add them.

    Most groups restrict 加人 to the owner and admins, and the bot is neither unless it
    created the group. That failure is Feishu 232017; pass the owner's/admin's
    ``user_key`` to act as them, or ask them to change 「谁可以添加群成员」 to 所有群成员.
    Check first with ``feishu_chat_get`` (``settings["谁可以加人"]``).

    Args:
        chat_id: Target group's chat_id (``oc_...``, from ``feishu_chat_find``).
        user_ids: Ids to add — max 50 users (or 5 bots) per call; duplicates are dropped.
        member_id_type: Id form of user_ids — open_id (default), union_id, user_id, or
            app_id (for bots).
        succeed_type: What to do about ids Feishu can't reach — 1 (default) adds everyone
            reachable and reports the rest; 0 fails the whole call over one bad id;
            2 fails strictly on any unusable id. Leave at 1 unless all-or-nothing matters.
        user_key: The owner's/admin's open_id, to add members as that person when the bot
            lacks the right (optional).
    """
    return _f.dumps_result(await _f.add_chat_members_impl(chat_id, user_ids, member_id_type, succeed_type, user_key))


async def feishu_chat_remove_members(
    chat_id: str,
    user_ids: list[str] | None = None,
    member_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Remove people (or bots) from a Feishu/Lark group (移出群成员).

    Use it to clean up a group after someone changes teams or a temporary discussion
    ends. Ids Feishu refused come back in ``invalid_ids`` rather than vanishing, so
    compare ``removed`` against what you asked for before reporting success.

    Two Feishu rules decide whether this works, and both surface as a ``hint``:
    only the **owner**, an admin, or the bot that **created** the group may remove other
    people (232017 — pass that person's ``user_key``; anyone may always remove
    themselves), and the **owner cannot be removed** at all (232076 — transfer ownership
    first). Removing someone is visible to the group and not undoable by this tool
    (they must be re-added), so confirm the right people with
    ``feishu_chat_list_members`` before calling.

    Args:
        chat_id: Target group's chat_id (``oc_...``, from ``feishu_chat_find``).
        user_ids: Ids to remove — max 50 users (or 5 bots) per call.
        member_id_type: Id form of user_ids — open_id (default), union_id, user_id, or
            app_id (for bots).
        user_key: The owner's/admin's open_id, to remove members as that person when the
            bot lacks the right (optional).
    """
    return _f.dumps_result(await _f.remove_chat_members_impl(chat_id, user_ids, member_id_type, user_key))


async def feishu_chat_list(whose: str = "bot", limit: int = 100, user_key: str = "") -> str:
    """List the Feishu/Lark groups the bot — or the asking person — belongs to (会话列表).

    The complement to ``feishu_chat_find``: search needs a name to look for, this needs
    nothing. Use it for 「我在哪些群」/「机器人进了哪些群」, or to sweep every group when there
    is no name to search by.

    ``whose`` is the argument to get right, because the endpoint is the same either way
    and only the token differs: ``"bot"`` lists the **bot's** groups, ``"me"`` lists the
    **caller's** own (which needs their authorization). Answering 「我在哪些群」 with the
    bot's list is a wrong answer that looks plausible.

    Returns ``[{chat_id, name, description, owner_id, owner_is_bot, external,
    chat_status, status_label}]``. Single chats (p2p) are never listed — Feishu's chat
    list is groups only. Paging runs in creation order on purpose: Feishu warns that
    paging an activity-ordered list can skip groups as the order shifts.

    Args:
        whose: ``"bot"`` (default) for the bot's groups, ``"me"`` for the caller's own.
        limit: Max groups to return (default 100, cap 1000); ``truncated`` says if more exist.
        user_key: The caller's open_id from ``<feishu_context>``. **Required** for
            ``whose="me"``; harmless otherwise.
    """
    return _f.dumps_result(await _f.list_chats_impl(whose, limit, user_key))


async def feishu_chat_announcement(chat_id: str, max_chars: int = 20000, user_key: str = "") -> str:
    """Read a Feishu/Lark group's **群公告** — the pinned notice board.

    A group announcement is a *document*, not a message, so it never appears in message
    history: this is the only way to read what a group's standing notice says (值班安排,
    入群须知, 本周重点).

    Returns the notice as plain ``text`` plus its ``blocks`` (``{block_id, type_name,
    text}``) for a targeted follow-up edit, and ``revision_id`` — the version the write
    tools lock against. An **empty** announcement is a normal answer (``empty: true``),
    not an error: every group has an announcement document even if nobody wrote in it.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``). Single
            chats (p2p) have no announcement.
        max_chars: Cap on the returned text (default 20000); ``truncated`` says if cut.
        user_key: The caller's open_id, used as a fallback identity when the bot lacks
            read access to the announcement doc (optional).
    """
    return _f.dumps_result(await _f.read_chat_announcement_impl(chat_id, max_chars, user_key))


async def feishu_chat_announcement_set(
    chat_id: str,
    content: str,
    replace: bool = True,
    user_key: str = "",
) -> str:
    """Write a Feishu/Lark group's **群公告** (设置群公告).

    Takes plain text or light Markdown headings (``# 标题``), one block per line — the
    same content shape as ``feishu_doc_append_content``.

    ``replace=True`` (default) rewrites the notice: the old body is deleted, then the new
    text written. ``replace=False`` appends, for adding a line to a standing notice
    without retyping it. Each write re-reads the announcement's ``revision_id`` because
    Feishu optimistically locks on it and a stale one is refused — the caller never has
    to think about that.

    Blank ``content`` is refused rather than treated as "clear it": use
    ``feishu_chat_announcement_clear`` to empty a notice, so wiping one is always
    something that was asked for. Most groups restrict 编辑群信息 to the owner and admins
    (Feishu 232002) — pass their ``user_key`` if the bot is refused.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        content: The notice text. ``# ``/``## `` become headings; other lines paragraphs.
        replace: True (default) replaces the whole notice; False appends to it.
        user_key: The owner's/admin's open_id, to write as that person when the group
            restricts editing (optional).
    """
    return _f.dumps_result(await _f.set_chat_announcement_impl(chat_id, content, replace, user_key))


async def feishu_chat_announcement_clear(chat_id: str, user_key: str = "") -> str:
    """Empty a Feishu/Lark group's **群公告**, deleting every line of it.

    Separate from ``feishu_chat_announcement_set`` because there is no undo: the previous
    notice is not recoverable through any tool here. Read it first with
    ``feishu_chat_announcement`` if it might be worth keeping a copy. Clearing an already
    empty announcement succeeds with ``deleted: 0``.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        user_key: The owner's/admin's open_id, when the group restricts 编辑群信息 (optional).
    """
    return _f.dumps_result(await _f.clear_chat_announcement_impl(chat_id, user_key))


async def feishu_chat_update(
    chat_id: str,
    name: str = "",
    description: str = "",
    avatar: str = "",
    add_member_permission: str = "",
    at_all_permission: str = "",
    edit_permission: str = "",
    membership_approval: str = "",
    chat_type: str = "",
    user_key: str = "",
) -> str:
    """Change a Feishu/Lark group's name, avatar, description or permissions (群设置变更).

    Every argument is optional and **only the ones you pass are sent**, so renaming a
    group cannot accidentally reset who may add members. ``share_card_permission`` is set
    automatically to match ``add_member_permission`` — Feishu requires the pair to agree
    and rejects the mismatch.

    Two related things are deliberately elsewhere: **全员禁言** is a different endpoint
    (``feishu_chat_mute``), and **转让群主** has its own tool (``feishu_chat_transfer_owner``)
    because it hands away control. Read the current state with ``feishu_chat_get`` first.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        name: New group name (公开群 needs ≥2 chars).
        description: New group description/topic.
        avatar: New group avatar as an ``image_key`` — upload it with
            ``feishu_chat_upload_avatar`` (a message-type image_key is rejected).
        add_member_permission: Who may add members — ``all_members`` (所有群成员) or
            ``only_owner`` (仅群主和管理员).
        at_all_permission: Who may @所有人 — ``all_members`` / ``only_owner``.
        edit_permission: Who may edit group info — ``all_members`` / ``only_owner``.
        membership_approval: Whether joining needs approval — ``approval_required`` (需审批)
            or ``no_approval_required`` (无需审批).
        chat_type: ``private`` (私有群) or ``public`` (公开群).
        user_key: The owner's/admin's open_id, to change settings as that person when the
            group restricts 编辑群信息 (optional).
    """
    return _f.dumps_result(
        await _f.update_chat_impl(
            chat_id,
            name,
            description,
            avatar,
            add_member_permission,
            at_all_permission,
            edit_permission,
            membership_approval,
            chat_type,
            user_key,
        )
    )


async def feishu_chat_upload_avatar(image_path: str, user_key: str = "") -> str:
    """Upload a local picture as a **group avatar** and return its ``image_key``.

    Needed because ``feishu_chat_update(avatar=…)`` takes an ``image_key``, and a group
    avatar must be uploaded with ``image_type="avatar"``. A key from
    ``feishu_message_upload_image`` (message type) uploads fine and is then rejected by
    the group update with 232021, which reads as a bad avatar rather than a wrong upload.

    Args:
        image_path: Absolute path to the picture (JPG/PNG/WEBP/GIF/BMP…, max 10MB).
        user_key: The caller's open_id (optional).
    """
    return _f.dumps_result(await _f.upload_chat_avatar_impl(image_path, user_key))


async def feishu_chat_mute(
    chat_id: str,
    setting: str,
    speaker_ids: list[str] | None = None,
    revoke_ids: list[str] | None = None,
    user_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Set who may speak in a Feishu/Lark group — 全员禁言 / 解除禁言 / 指定人员可发言.

    A **separate endpoint** from every other group setting, which is the trap worth
    knowing: the ``谁可以发言`` value that ``feishu_chat_get`` reads cannot be written
    through ``feishu_chat_update`` — that field is silently ignored there.

    Only the group owner (or the bot that created the group) may change this; Feishu
    answers 232017 otherwise, so pass the owner's ``user_key``. A group with a meeting in
    progress refuses the change (232092).

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        setting: ``only_owner`` for 全员禁言 (only owner+admins may post), ``all_members``
            to 解除禁言, or ``moderator_list`` to let only ``speaker_ids`` speak. The
            Chinese phrasings (``"全员禁言"`` / ``"解除禁言"`` / ``"指定人员"``) also work.
        speaker_ids: With ``moderator_list``, the people who **keep** the right to speak.
        revoke_ids: People whose speaking right is removed. Must not overlap ``speaker_ids``.
        user_id_type: Id form of the two lists — open_id (default), union_id, or user_id.
        user_key: The owner's open_id, to act as them (usually required).
    """
    return _f.dumps_result(
        await _f.update_chat_moderation_impl(chat_id, setting, speaker_ids, revoke_ids, user_id_type, user_key)
    )


async def feishu_chat_transfer_owner(
    chat_id: str,
    new_owner_id: str,
    user_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Hand a Feishu/Lark group over to a new owner (转让群主).

    Its own tool rather than a field on ``feishu_chat_update`` because the current owner
    **loses control** by it: afterwards they are an ordinary member (or admin) and cannot
    take the group back without the new owner's cooperation.

    The new owner must **already be a member** — Feishu answers 232012 otherwise, so add
    them with ``feishu_chat_add_members`` first. Only the current owner may transfer
    (232017), which normally means passing their ``user_key``.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        new_owner_id: The new owner's id — resolve a name with
            ``feishu_chat_list_members`` / ``feishu_contact_search`` first.
        user_id_type: Id form of new_owner_id — open_id (default), union_id, or user_id.
        user_key: The current owner's open_id, to transfer as them (usually required).
    """
    return _f.dumps_result(await _f.transfer_chat_owner_impl(chat_id, new_owner_id, user_id_type, user_key))


async def feishu_chat_dismiss(chat_id: str, confirm: str = "", user_key: str = "") -> str:
    """Dismiss (解散) a Feishu/Lark group — **irreversible**, and history is not kept.

    The most destructive call in the Feishu tool set: Feishu does not preserve the chat
    record, so the messages and files in it are gone and nothing here can undo it.
    Consider ``feishu_chat_remove_members`` or 归档 instead when the goal is just to stop
    a group being used.

    Requires ``confirm="解散群"``. That guard is there so a loosely-worded instruction
    ("把那个群清一下") cannot dissolve a group — say it explicitly, and check with
    ``feishu_chat_get`` that this is the intended group first. Only the owner (or the bot
    that created the group) may do it (232017); 232009 means it is already dissolved.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        confirm: Must be exactly ``"解散群"`` to proceed.
        user_key: The owner's open_id, to dismiss as them (usually required).
    """
    return _f.dumps_result(await _f.dismiss_chat_impl(chat_id, confirm, user_key))


async def feishu_chat_menu_get(chat_id: str, user_key: str = "") -> str:
    """Read a Feishu/Lark group's **群菜单** — the buttons along the bottom of the chat.

    Returns ``[{id, name, url, children}]``. The ``id`` matters: it is the only way to
    delete a menu, and ``feishu_chat_menu_add`` **appends** rather than replaces — so
    read this first if the intent is to change an existing menu, or the group ends up
    with two 「帮助」 buttons.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        user_key: The caller's open_id (optional).
    """
    return _f.dumps_result(await _f.get_chat_menu_impl(chat_id, user_key))


async def feishu_chat_menu_add(chat_id: str, menus: list[dict[str, Any]] | None = None, user_key: str = "") -> str:
    """Add buttons to a Feishu/Lark group's **群菜单** (群底部的快捷入口).

    ``menus`` is a flat list: ``[{"name": "值班表", "url": "https://…"}]``, or with
    ``"children"`` for a dropdown:
    ``[{"name": "常用", "children": [{"name": "报销", "url": "https://…"}]}]``.

    A menu **with children is a group heading**: it may not have its own ``url`` or
    ``image_key`` (Feishu's rule), and children cannot be added to an existing top-level
    menu afterwards — so build a dropdown in one call. Limits are 3 top-level menus with
    5 children each; this **appends**, so existing menus survive.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``). Only regular
            groups support menus.
        menus: The menus to add — each ``{name, url?, image_key?, children?}``. ``url``
            must start with http(s); ``image_key`` must have been uploaded by this bot
            (``feishu_message_upload_image``).
        user_key: The caller's open_id (optional).
    """
    return _f.dumps_result(await _f.add_chat_menu_impl(chat_id, menus, user_key))


async def feishu_chat_menu_delete(chat_id: str, menu_ids: list[str] | None = None, user_key: str = "") -> str:
    """Remove buttons from a Feishu/Lark group's **群菜单** by id.

    Takes the ``id`` values from ``feishu_chat_menu_get``, not menu names: two menus may
    share a name, and deleting the wrong button is immediately visible to everyone in the
    group. Deleting a top-level menu removes its children with it.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        menu_ids: Top-level menu ids to remove (from ``feishu_chat_menu_get``).
        user_key: The caller's open_id (optional).
    """
    return _f.dumps_result(await _f.delete_chat_menu_impl(chat_id, menu_ids, user_key))


async def feishu_chat_tabs(chat_id: str, user_key: str = "") -> str:
    """List a Feishu/Lark group's **群标签页** — the tabs pinned across the top.

    Returns ``[{tab_id, name, type, content}]``, including the built-in tabs (pin /
    会议纪要 / 任务 / 图片视频 …) that the API can only read. So a ``tab_id`` from here is
    not necessarily removable — only ``doc`` and ``url`` tabs are.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        user_key: The caller's open_id (optional).
    """
    return _f.dumps_result(await _f.list_chat_tabs_impl(chat_id, user_key))


async def feishu_chat_tab_add(
    chat_id: str,
    tab_name: str,
    tab_type: str = "url",
    content: str = "",
    user_key: str = "",
) -> str:
    """Pin a document or web page as a **群标签页** at the top of a Feishu/Lark group.

    What "把这份文档挂到群顶上" means: the tab sits beside 消息/Pin and opens the link for
    everyone in the group — more discoverable than a message that scrolls away.

    Only ``doc`` and ``url`` tabs can be created. Feishu's other tab types (pin, 会议纪要,
    任务, 图片视频 …) are built in and read-only, so asking for one is refused up front
    instead of failing as an opaque parameter error. Cap is 20 custom tabs per chat.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        tab_name: The tab's label (max 60 chars).
        tab_type: ``"url"`` (default) for a web page, or ``"doc"`` for a Feishu doc/sheet/
            bitable link.
        content: The link — must start with http(s). For ``doc``, the document's URL; the
            bot needs access to it (232051 otherwise).
        user_key: The owner's/admin's open_id, when the group restricts tab management
            (optional).
    """
    return _f.dumps_result(await _f.add_chat_tab_impl(chat_id, tab_name, tab_type, content, user_key))


async def feishu_chat_tab_delete(chat_id: str, tab_ids: list[str] | None = None, user_key: str = "") -> str:
    """Remove **群标签页** from a Feishu/Lark group by ``tab_id``.

    Ids come from ``feishu_chat_tabs``. Built-in tabs are refused by Feishu rather than
    silently reported as removed.

    Args:
        chat_id: The group's chat_id (``oc_...``, from ``feishu_chat_find``).
        tab_ids: Tab ids to remove (from ``feishu_chat_tabs``).
        user_key: The owner's/admin's open_id, when the group restricts tab management
            (optional).
    """
    return _f.dumps_result(await _f.delete_chat_tabs_impl(chat_id, tab_ids, user_key))
