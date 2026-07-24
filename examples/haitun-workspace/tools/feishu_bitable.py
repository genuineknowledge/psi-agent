"""Feishu/Lark bitable (多维表格) tools — list tables, read and create records.

Generic read/write over a Feishu base (bitable). Use to record structured data
(e.g. mentor feedback, logs, trackers) that the team can see in Feishu, and to
read it back for summaries.

The ``app_token`` is the segment in a ``feishu.cn/base/<app_token>`` URL. For a
wiki link (``feishu.cn/wiki/...``), resolve it with ``feishu_wiki_get_node``
first — its ``obj_token`` is the ``app_token`` when ``obj_type`` is ``bitable``.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``, the ``bitable:app``
scope, and the app added as a collaborator (editor) on the target base.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_bitable_list_tables(app_token: str, page_size: int = 100, page_token: str = "") -> str:
    """List the data tables inside a Feishu bitable (multi-dimensional table) app.

    Returns ``{table_id, name}`` for each table — you need a ``table_id`` before
    reading or creating records.

    Args:
        app_token: The base's app_token (from a feishu.cn/base/<app_token> URL).
        page_size: Max tables to return (default 100, max 100).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.list_bitable_tables_impl(app_token, page_size, page_token))


async def feishu_bitable_list_records(
    app_token: str,
    table_id: str,
    page_size: int = 100,
    page_token: str = "",
    filter: str = "",
    sort: str = "",
    field_names: str = "",
) -> str:
    """List records (rows) in a Feishu bitable table.

    Returns ``{record_id, fields}`` per record, plus ``has_more`` / ``page_token``.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        page_size: Max records per page (default 100, max 500).
        page_token: Pagination cursor from a previous call's has_more result (optional).
        filter: Optional Feishu filter expression (max 2000 chars).
        sort: Optional sort, e.g. '["日期 DESC"]'.
        field_names: Optional field allow-list, e.g. '["新人","反馈内容"]'.
    """
    return _f.dumps_result(
        await _f.list_bitable_records_impl(app_token, table_id, page_size, page_token, filter, sort, field_names)
    )


async def feishu_bitable_create_record(app_token: str, table_id: str, fields_json: str, user_key: str = "") -> str:
    """Create one record (row) in a Feishu bitable table.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        fields_json: A JSON object string mapping column names to values, e.g.
            '{"新人":"张三","Mentor":"李四","反馈内容":"进步明显","评分":4}'.
            Column names must match the table's fields.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to act as
            that user when the base is user-owned and the bot isn't a collaborator;
            empty uses the bot's tenant token.
    """
    return _f.dumps_result(await _f.create_bitable_record_impl(app_token, table_id, fields_json, user_key))


async def feishu_bitable_delete_records(app_token: str, table_id: str, record_ids: str, user_key: str = "") -> str:
    """Delete records (rows) from a Feishu bitable table by id.

    Use to remove specific rows — e.g. Feishu's default empty rows on a new table.
    Get record_ids from ``feishu_bitable_list_records``. Deletes in batches of 500.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        record_ids: Comma-separated record ids to delete, e.g. "recAAA,recBBB".
        user_key: The sender's open_id; pass it to act as that user (see create_record).
    """
    return _f.dumps_result(await _f.delete_bitable_records_impl(app_token, table_id, record_ids, user_key))


async def feishu_bitable_clear_table(app_token: str, table_id: str, user_key: str = "") -> str:
    """Delete ALL records (rows) in a Feishu bitable table.

    Pages through every record and batch-deletes them — useful to wipe a table's
    default empty rows (or all data) before writing fresh records. Fields/columns
    are NOT touched (use ``feishu_bitable_delete_fields`` for columns).

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        user_key: The sender's open_id; pass it to act as that user (see create_record).
    """
    return _f.dumps_result(await _f.clear_bitable_table_impl(app_token, table_id, user_key))


async def feishu_bitable_list_fields(app_token: str, table_id: str) -> str:
    """List a Feishu bitable table's fields (columns).

    Returns ``{field_id, name, type, is_primary}`` per field. Use this to find the
    field_id of columns you want to remove (e.g. Feishu's default placeholder
    columns) before calling ``feishu_bitable_delete_fields``.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
    """
    return _f.dumps_result(await _f.list_bitable_fields_impl(app_token, table_id))


async def feishu_bitable_delete_fields(app_token: str, table_id: str, field_ids: str, user_key: str = "") -> str:
    """Delete fields (columns) from a Feishu bitable table by id.

    Use to remove Feishu's default empty/placeholder columns. Get field_ids from
    ``feishu_bitable_list_fields``. The primary (index) column cannot be deleted —
    Feishu returns error 1254046 for it.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        field_ids: Comma-separated field ids to delete, e.g. "fldAAA,fldBBB".
        user_key: The sender's open_id; pass it to act as that user (see create_record).
    """
    return _f.dumps_result(await _f.delete_bitable_fields_impl(app_token, table_id, field_ids, user_key))


async def feishu_bitable_create_role(app_token: str, role_name: str, table_roles_json: str, user_key: str = "") -> str:
    """Create a custom role (自定义角色) on a bitable — the key to "one base, roles see different content".

    A role controls, per table, whether members can read/edit, and optionally which
    *records* (rows) and *fields* (columns) they see. Assign people to the role with
    ``feishu_bitable_add_role_member``. This lets everyone open the same base while each
    role sees only its slice — the cleanest way to do "全员可查但按角色显示不同内容".
    Requires advanced permission (高级权限) enabled on the base.

    ``table_roles_json`` is a JSON array, one object per table, e.g.:
    ``[{"table_id": "tblXXX", "table_perm": 1}]`` where table_perm is
    0=none, 1=view, 2=edit-added-records, 4=edit-all. For per-row visibility add
    ``"rec_rule": {"conditions": [...], "perm": 1}``; for per-field control add
    ``"field_perm": {"fld1": 1, "fld2": 2}``. See the Feishu bitable advanced-permission
    docs for the full shape.

    Args:
        app_token: The base's app_token (from a feishu.cn/base/<app_token> URL).
        role_name: Display name for the new role.
        table_roles_json: JSON array of per-table permission objects (see above).
        user_key: The sender's open_id; pass it to act as that user (see create_record).
    """
    return _f.dumps_result(await _f.create_bitable_role_impl(app_token, role_name, table_roles_json, user_key))


async def feishu_bitable_list_roles(
    app_token: str, page_size: int = 100, page_token: str = "", user_key: str = ""
) -> str:
    """List the custom roles defined on a bitable (each with its role_id and per-table perms).

    Use this to find a ``role_id`` before assigning members with
    ``feishu_bitable_add_role_member``.

    Args:
        app_token: The base's app_token.
        page_size: Max roles to return (default 100).
        page_token: Pagination cursor from a previous call's has_more result (optional).
        user_key: The sender's open_id; pass it to act as that user (see create_record).
    """
    return _f.dumps_result(await _f.list_bitable_roles_impl(app_token, page_size, page_token, user_key))


async def feishu_bitable_add_role_member(
    app_token: str, role_id: str, member_id: str, member_id_type: str = "open_id", user_key: str = ""
) -> str:
    """Assign a user to a bitable custom role — that person then sees the role's rows/fields.

    Args:
        app_token: The base's app_token.
        role_id: The role's id (from ``feishu_bitable_list_roles`` or create_role).
        member_id: The user to assign (form matches member_id_type).
        member_id_type: Id form — open_id (default), union_id, user_id.
        user_key: The sender's open_id; pass it to act as that user (see create_record).
    """
    return _f.dumps_result(
        await _f.add_bitable_role_member_impl(app_token, role_id, member_id, member_id_type, user_key)
    )
