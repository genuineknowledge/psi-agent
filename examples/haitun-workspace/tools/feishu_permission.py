"""Feishu/Lark drive permission tools — make a doc public and give people different access.

Use these to publish a Feishu artifact (docx/sheet/bitable/wiki/file) that everyone
can see, while letting different people or groups have different access — e.g. add the
whole company (a department) with ``view`` for "全员可查", and add specific owners with
``edit``/``full_access``. This is per-artifact access control; for "one base, different
roles see different rows/fields" use the bitable role tools in ``feishu_bitable``.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET`` and a permission scope such as
``drive:drive`` (or ``bitable:app`` / ``wiki:wiki`` for those types). Pass ``user_key`` to
act as the file's owner when the bot itself isn't a collaborator.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_permission_add_member(
    token: str,
    obj_type: str,
    member_id: str,
    perm: str = "view",
    member_type: str = "openid",
    member_kind: str = "user",
    need_notification: bool = False,
    user_key: str = "",
) -> str:
    """Grant a user/chat/department a permission level on a Feishu file (make it visible/editable).

    To make an artifact company-wide readable ("全员可查"), add the top department with
    ``member_type="opendepartmentid"``, ``member_kind="department"``, ``perm="view"``.
    To give a specific person edit rights, use their open_id with ``perm="edit"``.

    Args:
        token: The file's token (from its URL; for a wiki node use its obj_token).
        obj_type: Object type — one of docx, doc, sheet, bitable, file, wiki, folder.
        member_id: The id of the member to add (form matches member_type).
        perm: Permission level — view (default), edit, or full_access.
        member_type: Id form — openid (default), userid, unionid, openchat, opendepartmentid,
            email, groupid, wikispaceid.
        member_kind: Member kind — user (default), chat, department, or group.
        need_notification: If true, notify the member they were granted access (default false).
        user_key: The sender's open_id; pass it to act as that user (needed when the file
            is user-owned and the bot isn't a collaborator). Empty uses the bot's tenant token.
    """
    return _f.dumps_result(
        await _f.add_permission_member_impl(
            token, obj_type, member_id, perm, member_type, member_kind, need_notification, user_key
        )
    )


async def feishu_permission_list_members(token: str, obj_type: str, user_key: str = "") -> str:
    """List everyone with an explicit permission on a Feishu file (who can see or edit it).

    Args:
        token: The file's token (from its URL).
        obj_type: Object type — one of docx, doc, sheet, bitable, file, wiki, folder.
        user_key: The sender's open_id; pass it to read as that user. Empty uses tenant token.
    """
    return _f.dumps_result(await _f.list_permission_members_impl(token, obj_type, user_key))


async def feishu_permission_remove_member(
    token: str,
    obj_type: str,
    member_id: str,
    member_type: str = "openid",
    member_kind: str = "user",
    user_key: str = "",
) -> str:
    """Revoke a user/chat/department's permission on a Feishu file.

    Args:
        token: The file's token (from its URL).
        obj_type: Object type — one of docx, doc, sheet, bitable, file, wiki, folder.
        member_id: The id of the member to remove (form matches member_type).
        member_type: Id form — openid (default), userid, unionid, openchat, opendepartmentid,
            email, groupid, wikispaceid.
        member_kind: Member kind — user (default), chat, department, or group.
        user_key: The sender's open_id; pass it to act as that user. Empty uses tenant token.
    """
    return _f.dumps_result(
        await _f.delete_permission_member_impl(token, obj_type, member_id, member_type, member_kind, user_key)
    )
