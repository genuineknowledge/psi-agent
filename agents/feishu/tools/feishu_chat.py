"""Feishu/Lark chat (group) tools that need real orchestration.

Most group endpoints are now data, not code: 建群/拉人/踢人, group settings, 禁言,
转让群主, 解散群, 群菜单 and 群标签页 live in ``skills/feishu-chat/SKILL.md`` and run
through the generic ``feishu_api`` tool, which validates the call against that table
before it is sent.

What stays here is what a table cannot express:

- ``feishu_chat_find_member`` — pages the whole roster and matches by name, so the
  caller gets the person they asked for instead of the entire group in context.
- ``feishu_chat_get`` — Feishu answers a **non-member** with only name/avatar/counts
  and still returns 200, so a thin result has to be marked ``partial`` rather than
  read as "这个群没有群主".
- ``feishu_chat_announcement`` / ``_set`` / ``_clear`` — the announcement is a
  *document*: reading it means fetching its metadata and paging its blocks, and
  writing it means threading ``revision_id`` through an optimistic lock.
- ``feishu_chat_upload_avatar`` — multipart upload; a JSON body cannot carry a file
  handle, and a group avatar needs ``image_type="avatar"``.

Pair with ``feishu_message`` (send / search messages) and, for reply / list /
表情回应 / 置顶 / 转发, ``feishu_api`` plus the ``feishu-message`` skill's endpoint table.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


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
        chat_id: The group's chat_id (from a 群名搜索 via ``feishu_api``). The person must be a member.
        name: Person's name to match. Empty returns the whole roster.
        exact: When true, match the name exactly; otherwise substring match.
        member_id_type: Id form to return — open_id (default), union_id, or user_id.
    """
    return _f.dumps_result(await _f.find_member_id_impl(chat_id, name, exact, member_id_type))


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

    Feishu answers a **non-member** caller with only the name, avatar, counts and status.
    "Non-member" is about whose token asked: the bot is not in most groups a person is in,
    so a stub does not mean the group is unreadable. Passing ``user_key`` makes the tool
    retry as that person, which returns the full details whenever they are in the group.
    A result that is still ``partial=true`` carries ``partial_because`` and ``to_see_more``
    — report those rather than reading the stub as "这个群没有群主/没人"; ``user_count`` in a
    partial result is a non-member's view, not the real headcount.

    Args:
        chat_id: The group's chat_id (``oc_...``, from a 群名搜索 via ``feishu_api``).
        user_id_type: Id form for owner/admin ids — open_id (default), union_id, or user_id.
        user_key: The caller's open_id (from ``<feishu_context>``). Pass it whenever you
            have it: for a group the bot isn't in but the person is, this is what turns a
            stub into the real details.
    """
    return _f.dumps_result(await _f.get_chat_impl(chat_id, user_id_type, user_key))


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
        chat_id: The group's chat_id (``oc_...``, from a 群名搜索 via ``feishu_api``). Single
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
        chat_id: The group's chat_id (``oc_...``, from a 群名搜索 via ``feishu_api``).
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
        chat_id: The group's chat_id (``oc_...``, from a 群名搜索 via ``feishu_api``).
        user_key: The owner's/admin's open_id, when the group restricts 编辑群信息 (optional).
    """
    return _f.dumps_result(await _f.clear_chat_announcement_impl(chat_id, user_key))


async def feishu_chat_upload_avatar(image_path: str, user_key: str = "") -> str:
    """Upload a local picture as a **group avatar** and return its ``image_key``.

    Needed because the group-update endpoint (``PUT /open-apis/im/v1/chats/:chat_id``,
    see the ``feishu-chat`` skill) takes an ``image_key`` for ``avatar``, and a group
    avatar must be uploaded with ``image_type="avatar"``. A key from
    ``feishu_message_upload_image`` (message type) uploads fine and is then rejected by
    the group update with 232021, which reads as a bad avatar rather than a wrong upload.

    Args:
        image_path: Absolute path to the picture (JPG/PNG/WEBP/GIF/BMP…, max 10MB).
        user_key: The caller's open_id (optional).
    """
    return _f.dumps_result(await _f.upload_chat_avatar_impl(image_path, user_key))
