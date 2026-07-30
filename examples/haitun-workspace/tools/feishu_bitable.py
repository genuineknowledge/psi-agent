"""Feishu/Lark bitable (多维表格) tools — create bases/tables/fields, read and write records.

Generic read/write over a Feishu base (bitable). Use to record structured data
(e.g. mentor feedback, logs, trackers) that the team can see in Feishu, and to
read it back for summaries. When no base exists yet, build one from scratch with
``feishu_bitable_create_app`` → ``feishu_bitable_create_table`` →
``feishu_bitable_create_record``.

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


async def feishu_bitable_create_app(
    name: str, folder_token: str = "", time_zone: str = "", user_key: str = "", identity: str = ""
) -> str:
    """Create a NEW Feishu bitable (多维表格) — the base itself, when none exists yet.

    Every other bitable tool needs an ``app_token`` that already exists; this is what
    produces one. Returns ``app_token``, ``url`` (share this with the user) and
    ``default_table_id`` — the auto-created first table, which has only a placeholder
    index column. Typical build-a-tracker flow:

    ``feishu_bitable_create_app`` → ``feishu_bitable_create_table`` (with your real
    columns) → ``feishu_bitable_create_record`` per row. The default table can be left
    alone, or cleaned up with ``feishu_bitable_clear_table`` /
    ``feishu_bitable_delete_fields``.

    Args:
        name: Display name for the new base (max 255 chars).
        folder_token: Optional Drive folder to create it in; empty puts it at the root
            of the owner's 云空间. With the bot's own token only folders the app created
            are accepted.
        time_zone: Optional document time zone, e.g. "Asia/Shanghai".
        user_key: The sender's open_id (from ``<feishu_context>``), identifying whose
            authorization and remembered ownership choice apply. A bot-owned base lives
            in the bot's space, so the user must be given access separately.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.create_bitable_app_impl(name, folder_token, time_zone, user_key, identity))


async def feishu_bitable_create_table(
    app_token: str,
    table_name: str,
    fields_json: str = "",
    default_view_name: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Create a data table (数据表) with its columns inside an existing bitable.

    Define the columns up front in ``fields_json`` — a JSON array of field objects, e.g.
    ``[{"field_name":"合同编号","type":1},{"field_name":"金额","type":2},
    {"field_name":"状态","type":3,"property":{"options":[{"name":"生效","color":0},
    {"name":"到期","color":1}]}},{"field_name":"到期日","type":5},
    {"field_name":"负责人","type":11}]``.

    ``type`` is Feishu's field-type integer: 1 文本, 2 数字, 3 单选, 4 多选, 5 日期,
    7 复选框, 11 人员, 13 电话, 15 超链接, 17 附件, 18 单向关联, 20 公式,
    21 双向关联, 22 地理位置, 23 群组, 1001 创建时间, 1002 最后更新时间,
    1003 创建人, 1004 修改人, 1005 自动编号 (19 查找引用 cannot be created).
    The FIRST field becomes the index (primary) column and must be one of
    1/2/5/13/15/20/22 — put a text key column first.

    Omit ``fields_json`` to get a table with only a placeholder index column, then add
    columns one at a time with ``feishu_bitable_create_field``.

    Args:
        app_token: The base's app_token (from ``feishu_bitable_create_app`` or a
            feishu.cn/base/<app_token> URL).
        table_name: Name of the new table (1-100 chars; ``/ \\ ? * : [ ]`` not allowed).
        fields_json: JSON array of field objects, 1-300 entries (see above).
        default_view_name: Optional name for the table's default grid view. Feishu only
            accepts it together with fields_json.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(
        await _f.create_bitable_table_impl(app_token, table_name, fields_json, default_view_name, user_key, identity)
    )


async def feishu_bitable_create_field(
    app_token: str,
    table_id: str,
    field_name: str,
    field_type: int = 1,
    property_json: str = "",
    ui_type: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Add one field (column) to an existing Feishu bitable table.

    Use this to extend a table someone else built, or to fill in columns after creating
    a table without ``fields_json``. To define many columns at once, prefer
    ``feishu_bitable_create_table``.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        field_name: Column name.
        field_type: Feishu field-type integer — 1 文本 (default), 2 数字, 3 单选,
            4 多选, 5 日期, 7 复选框, 11 人员, 13 电话, 15 超链接, 17 附件,
            20 公式, 22 地理位置, 1001 创建时间, 1005 自动编号 (19 查找引用 is not
            creatable).
        property_json: Optional type-specific settings as a JSON object — select
            options ``{"options":[{"name":"高","color":0}]}`` (color 0-54), number
            format ``{"formatter":"0.00"}``, date format
            ``{"date_formatter":"yyyy-MM-dd"}``, person multi-select
            ``{"multiple":true}``.
        ui_type: Optional display variant, e.g. "Progress", "Currency", "Rating",
            "Email", "Barcode".
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(
        await _f.create_bitable_field_impl(
            app_token, table_id, field_name, field_type, property_json, ui_type, user_key, identity
        )
    )


async def feishu_bitable_update_field(
    app_token: str,
    table_id: str,
    field_id: str,
    field_name: str = "",
    field_type: int = 0,
    property_json: str = "",
    ui_type: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Change a column's definition — rename it, change its type, edit its options.

    This is how you fix a column that was built wrong **without losing its data**:
    deleting and re-creating the column throws away every value in it. Typical uses:
    rename "备注" to "审批意见", add an option to a 单选 column, turn a 文本 column into
    a 数字 one, switch a 人员 column to allow multiple people.

    Get ``field_id`` from ``feishu_bitable_list_fields``. Feishu replaces the whole
    field definition on update, so anything you leave out is read back from the table
    and carried over rather than reset — including the existing ``property`` when the
    type is unchanged. When you *do* pass ``property_json``, it replaces the old
    settings wholesale, so include every option you want to keep.

    Type 19 (查找引用) can't be set, and the table's index (primary) column is limited
    to types 1/2/5/13/15/20/22 — both are refused here rather than by Feishu.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        field_id: The column to change (from ``feishu_bitable_list_fields``).
        field_name: New column name. Omit to keep the current one.
        field_type: New Feishu field-type integer. Omit (0) to keep the current type.
        property_json: Type-specific settings as a JSON object — select options
            ``{"options":[{"name":"高","color":0}]}``, number format
            ``{"formatter":"0.00"}``, date format ``{"date_formatter":"yyyy-MM-dd"}``,
            person multi-select ``{"multiple":true}``. Replaces the old settings.
        ui_type: Optional display variant, e.g. "Progress", "Currency", "Rating".
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who performs the edit (see create_record).
    """
    return _f.dumps_result(
        await _f.update_bitable_field_impl(
            app_token, table_id, field_id, field_name, field_type, property_json, ui_type, user_key, identity
        )
    )


async def feishu_bitable_create_tables(app_token: str, table_names: str, user_key: str = "", identity: str = "") -> str:
    """Create several empty data tables in a bitable at once (names only, no columns).

    Handy when a base needs a fixed set of sheets — "合同/付款/发票三张表". Each table
    comes with only a placeholder index column; add real columns with
    ``feishu_bitable_create_field``. To create ONE table **with its columns defined
    up front**, use ``feishu_bitable_create_table`` instead — that is usually what you
    want. Max 50 per call; a base holds at most 100 tables plus dashboards.

    Args:
        app_token: The base's app_token.
        table_names: Comma-separated table names, e.g. "合同,付款,发票". Names are
            1-100 chars and cannot contain ``/ \\ ? * : [ ]``.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(await _f.create_bitable_tables_impl(app_token, table_names, user_key, identity))


async def feishu_bitable_delete_tables(app_token: str, table_ids: str, user_key: str = "", identity: str = "") -> str:
    """Delete whole data tables from a bitable — the table and ALL its rows and columns.

    Destructive and not undoable through the API: to empty a table but keep its
    structure use ``feishu_bitable_clear_table``, and to drop single columns use
    ``feishu_bitable_delete_fields``. Confirm with the user before deleting a table
    that holds data. A base must keep at least one table — Feishu refuses to delete
    the last one (1254034). Max 50 per call.

    Args:
        app_token: The base's app_token.
        table_ids: Comma-separated table ids to delete, e.g. "tblAAA,tblBBB"
            (from ``feishu_bitable_list_tables``).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who performs the deletion.
    """
    return _f.dumps_result(await _f.delete_bitable_tables_impl(app_token, table_ids, user_key, identity))


async def feishu_bitable_get_app(app_token: str, user_key: str = "") -> str:
    """Read a bitable's own metadata: its name, time zone, and whether 高级权限 is on.

    Two practical uses: confirming an ``app_token`` actually resolves to a base the
    bot/user can see before writing to it, and checking ``is_advanced`` — custom roles
    (``feishu_bitable_create_role``) require advanced permission to be enabled, which
    ``feishu_bitable_update_app`` can turn on.

    Args:
        app_token: The base's app_token (from a feishu.cn/base/<app_token> URL).
        user_key: The sender's open_id (from ``<feishu_context>``).
    """
    return _f.dumps_result(await _f.get_bitable_app_impl(app_token, user_key))


async def feishu_bitable_update_app(
    app_token: str, name: str = "", is_advanced: str = "", user_key: str = "", identity: str = ""
) -> str:
    """Rename a bitable and/or turn 高级权限 (advanced permission) on or off.

    Renaming here changes the base's own title (what shows in 云空间), not a data
    table's name. Enabling ``is_advanced`` is the prerequisite for per-role record and
    field visibility via ``feishu_bitable_create_role``.

    Settings you omit are left alone. Note Feishu applies the rename first and the
    permission toggle second, so a partial change is possible — the result's
    ``changed`` list reports what was sent.

    Advanced permission cannot be enabled on a base that lives in a wiki or is
    embedded in a doc/sheet (1254301, reported back with a hint).

    Args:
        app_token: The base's app_token.
        name: New base name (max 100 chars, no ``? / \\ * : [ ]``). Omit to keep it.
        is_advanced: ``"true"`` to enable advanced permission, ``"false"`` to disable.
            Omit to leave the current setting untouched.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who performs the change.
    """
    return _f.dumps_result(await _f.update_bitable_app_impl(app_token, name, is_advanced, user_key, identity))


async def feishu_bitable_copy_app(
    app_token: str,
    name: str = "",
    folder_token: str = "",
    without_content: bool = False,
    time_zone: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Duplicate an entire bitable — tables, columns and (optionally) the data.

    This is the **template** move: keep one well-built ledger and copy it per project
    or per month instead of rebuilding tables and columns from scratch with
    ``create_app`` → ``create_table``. Pass ``without_content=True`` to copy only the
    structure (empty tables with the same columns), which is what you usually want
    from a template.

    Returns the new base's ``app_token`` and ``url`` — send the url to the user.
    Rate-limited to 20 copies per minute; a base already being copied answers 1254036
    and the result carries a hint to retry.

    Args:
        app_token: The base to copy from.
        name: Name for the copy. Omit to let Feishu name it.
        folder_token: Optional Drive folder to place the copy in; with the bot's own
            token only folders the app created are accepted.
        without_content: True copies structure only (no rows). Default False copies
            the data too.
        time_zone: Optional time zone for the copy, e.g. "Asia/Shanghai".
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the copy (see create_record).
    """
    return _f.dumps_result(
        await _f.copy_bitable_app_impl(app_token, name, folder_token, without_content, time_zone, user_key, identity)
    )


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


async def feishu_bitable_search_records(
    app_token: str,
    table_id: str,
    filter_json: str = "",
    sort_json: str = "",
    field_names: str = "",
    view_id: str = "",
    page_size: int = 100,
    page_token: str = "",
    automatic_fields: bool = False,
    user_key: str = "",
) -> str:
    """Find records (rows) in a Feishu bitable table by structured conditions.

    Prefer this over ``feishu_bitable_list_records`` whenever you are looking for
    *particular* rows — "张三那几行", "状态是进行中且金额大于一万的", "负责人为空的" —
    and especially when you need a ``record_id`` to feed
    ``feishu_bitable_update_record`` / ``feishu_bitable_delete_records``. Feishu's own
    docs treat this as the way to obtain record ids; list_records is the plain
    full-table page-through.

    ``filter_json`` is a JSON object with a ``conjunction`` (``"and"`` / ``"or"``,
    required by Feishu) and up to 50 ``conditions``:
    ``{"conjunction":"and","conditions":[{"field_name":"状态","operator":"is",
    "value":["进行中"]},{"field_name":"金额","operator":"isGreater","value":["10000"]}]}``.
    Operators: ``is``, ``isNot``, ``contains``, ``doesNotContain``, ``isEmpty``,
    ``isNotEmpty``, ``isGreater``, ``isGreaterEqual``, ``isLess``, ``isLessEqual``
    (``value`` is always an array of **strings**, up to 10; omit it for isEmpty /
    isNotEmpty). Date columns don't accept isNot / contains / doesNotContain /
    isGreaterEqual / isLessEqual.

    Get the exact ``field_name`` spellings from ``feishu_bitable_list_fields`` first —
    a wrong column name silently matches nothing.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        filter_json: Conditions object (see above). Empty returns everything.
        sort_json: Optional JSON array, e.g. '[{"field_name":"日期","desc":true}]'.
        field_names: Optional JSON array limiting the columns returned,
            e.g. '["状态","负责人"]'.
        view_id: Optional view to search within. Feishu **ignores** it when
            filter_json/sort_json is given, so the tool refuses that combination
            rather than quietly searching the whole table.
        page_size: Rows per page (default 100, max 500).
        page_token: Pagination cursor from a previous call's has_more result.
        automatic_fields: Also return created_time / last_modified_time / created_by /
            last_modified_by (default false).
        user_key: The sender's open_id (from ``<feishu_context>``).
    """
    return _f.dumps_result(
        await _f.search_bitable_records_impl(
            app_token,
            table_id,
            filter_json,
            sort_json,
            field_names,
            view_id,
            page_size,
            page_token,
            automatic_fields,
            user_key,
        )
    )


async def feishu_bitable_get_record(
    app_token: str,
    table_id: str,
    record_id: str,
    automatic_fields: bool = False,
    user_key: str = "",
) -> str:
    """Read ONE record (row) of a Feishu bitable by its record_id.

    Use when you already have the ``record_id`` and just want that row's current
    values — e.g. to show the user what a cell holds now, or to check the result of
    an edit. To *find* rows by their content use ``feishu_bitable_search_records``;
    to read many rows use ``feishu_bitable_list_records``.

    Returns the row's ``fields``, its ``url`` (a direct link to that record), and —
    with ``automatic_fields`` — who created/last changed it and when.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        record_id: The row to read (from ``feishu_bitable_search_records``).
        automatic_fields: Also return created_by / created_time / last_modified_by /
            last_modified_time (default false).
        user_key: The sender's open_id (from ``<feishu_context>``).
    """
    return _f.dumps_result(await _f.get_bitable_record_impl(app_token, table_id, record_id, automatic_fields, user_key))


async def feishu_bitable_create_records(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> str:
    """Create MANY records (rows) in a Feishu bitable table in one call.

    Use this instead of calling ``feishu_bitable_create_record`` in a loop whenever
    you have more than one row to write — filling a fresh ledger, importing a list,
    logging a batch of results. One call rather than N is faster and avoids Feishu's
    per-app rate limit. Writes in batches of 500 (Feishu's per-call cap); a table
    holds at most 20000 records in total.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        records_json: JSON array of rows, either bare column maps
            '[{"姓名":"张三","状态":"在读"},{"姓名":"李四"}]' or Feishu's wrapper form
            '[{"fields":{"姓名":"张三"}}]' — both are accepted. Column names must match
            the table's fields.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
        validate_fields: Check the column names against the table first (default true).
            Feishu silently drops unknown column names and still reports success — this
            is what stops "wrote 22 rows" from meaning "22 rows with only the key column
            filled in".
    """
    return _f.dumps_result(
        await _f.create_bitable_records_impl(app_token, table_id, records_json, user_key, identity, validate_fields)
    )


async def feishu_bitable_create_record(
    app_token: str, table_id: str, fields_json: str, user_key: str = "", identity: str = ""
) -> str:
    """Create one record (row) in a Feishu bitable table.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        fields_json: A JSON object string mapping column names to values, e.g.
            '{"新人":"张三","Mentor":"李四","反馈内容":"进步明显","评分":4}'.
            Column names must match the table's fields.
        user_key: The sender's open_id (from ``<feishu_context>``). A user-owned base
            generally needs that user's identity, since the bot isn't a collaborator.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.create_bitable_record_impl(app_token, table_id, fields_json, user_key, identity))


async def feishu_bitable_update_record(
    app_token: str,
    table_id: str,
    record_id: str,
    fields_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> str:
    """Change cell values in an existing record (row) of a Feishu bitable table.

    This is the tool for "改一下某一行的某个格子" — updating a status, correcting a
    number, filling a blank. The update is **incremental**: only the columns you pass
    are written and every other cell on that row keeps its value. Pass ``null`` as a
    value to clear a cell.

    Find the ``record_id`` with ``feishu_bitable_list_records`` (optionally with its
    ``filter`` to locate the row by its key column). To change the same or different
    cells on many rows, use ``feishu_bitable_update_records`` instead — one call
    rather than one per row.

    Values follow the column's type: text as a plain string, 数字 as a number,
    单选 as the option name, 多选 as an array of names, 日期 as a **millisecond**
    epoch timestamp, 复选框 as true/false, 人员 as ``[{"id":"ou_..."}]``,
    超链接 as ``{"text":"...","link":"https://..."}``, 附件 as
    ``[{"file_token":"..."}]``, 关联 as an array of record ids, 地理位置 as
    ``"lat,lng"``. Computed columns (公式, 查找引用, 创建时间, 自动编号) cannot be
    written.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        record_id: The row to change (from ``feishu_bitable_list_records``).
        fields_json: A JSON object of the columns to change and their new values, e.g.
            '{"状态":"已完成","评分":5}'. Columns you leave out are not touched.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who performs the edit (see create_record).
        validate_fields: Check the column names against the table first (default true).
            Feishu silently ignores unknown column names and still reports success, so
            this is what catches "wrote it, cell unchanged". Turn off only when the
            names are already known good.
    """
    return _f.dumps_result(
        await _f.update_bitable_record_impl(
            app_token, table_id, record_id, fields_json, user_key, identity, validate_fields
        )
    )


async def feishu_bitable_update_records(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> str:
    """Update cells across MANY records of a Feishu bitable table in one call.

    Same semantics as ``feishu_bitable_update_record`` (incremental — untouched
    columns keep their values, ``null`` clears a cell) but each row can get its own
    set of changes. Use this for sweeps like "把这 20 行的状态改成已完成" instead of
    looping the single-record tool. Updates in batches of 1000 (Feishu's per-call
    limit).

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        records_json: JSON array of ``{"record_id": ..., "fields": {...}}`` objects,
            e.g. '[{"record_id":"recA","fields":{"状态":"已完成"}},
            {"record_id":"recB","fields":{"状态":"进行中","负责人":[{"id":"ou_x"}]}}]'.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who performs the edit (see create_record).
        validate_fields: Check every column name against the table first (default
            true) — Feishu drops unknown names silently and still returns success.
    """
    return _f.dumps_result(
        await _f.update_bitable_records_impl(app_token, table_id, records_json, user_key, identity, validate_fields)
    )


async def feishu_bitable_delete_records(
    app_token: str, table_id: str, record_ids: str, user_key: str = "", identity: str = ""
) -> str:
    """Delete records (rows) from a Feishu bitable table by id.

    Use to remove specific rows — e.g. Feishu's default empty rows on a new table.
    Get record_ids from ``feishu_bitable_list_records``. Deletes in batches of 500.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        record_ids: Comma-separated record ids to delete, e.g. "recAAA,recBBB".
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(await _f.delete_bitable_records_impl(app_token, table_id, record_ids, user_key, identity))


async def feishu_bitable_clear_table(app_token: str, table_id: str, user_key: str = "", identity: str = "") -> str:
    """Delete ALL records (rows) in a Feishu bitable table.

    Pages through every record and batch-deletes them — useful to wipe a table's
    default empty rows (or all data) before writing fresh records. Fields/columns
    are NOT touched (use ``feishu_bitable_delete_fields`` for columns).

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(await _f.clear_bitable_table_impl(app_token, table_id, user_key, identity))


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


async def feishu_bitable_delete_fields(
    app_token: str, table_id: str, field_ids: str, user_key: str = "", identity: str = ""
) -> str:
    """Delete fields (columns) from a Feishu bitable table by id.

    Use to remove Feishu's default empty/placeholder columns. Get field_ids from
    ``feishu_bitable_list_fields``. The primary (index) column cannot be deleted —
    Feishu returns error 1254046 for it.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        field_ids: Comma-separated field ids to delete, e.g. "fldAAA,fldBBB".
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(await _f.delete_bitable_fields_impl(app_token, table_id, field_ids, user_key, identity))


async def feishu_bitable_create_role(
    app_token: str, role_name: str, table_roles_json: str, user_key: str = "", identity: str = ""
) -> str:
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
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(
        await _f.create_bitable_role_impl(app_token, role_name, table_roles_json, user_key, identity)
    )


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
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(await _f.list_bitable_roles_impl(app_token, page_size, page_token, user_key))


async def feishu_bitable_add_role_member(
    app_token: str,
    role_id: str,
    member_id: str,
    member_id_type: str = "open_id",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Assign a user to a bitable custom role — that person then sees the role's rows/fields.

    Args:
        app_token: The base's app_token.
        role_id: The role's id (from ``feishu_bitable_list_roles`` or create_role).
        member_id: The user to assign (form matches member_id_type).
        member_id_type: Id form — open_id (default), union_id, user_id.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(
        await _f.add_bitable_role_member_impl(app_token, role_id, member_id, member_id_type, user_key, identity)
    )
