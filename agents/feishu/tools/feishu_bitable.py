"""Feishu/Lark bitable (多维表格) tools — the writes a table row cannot express.

Most bitable endpoints now live as data in ``skills/feishu-bitable/SKILL.md`` and go out
through ``feishu_api``: creating a base, listing/reading tables, fields, records and roles,
and the single-record writes. What stays here is the handful whose payload needs code —
column definitions to validate, a whole-object PUT to pre-fill, a paged delete loop.

Read the skill for the endpoint table; call ``feishu_api`` for anything not listed below.

The ``app_token`` is the segment in a ``feishu.cn/base/<app_token>`` URL. For a
wiki link (``feishu.cn/wiki/...``), resolve it with ``feishu_api`` on
``GET /open-apis/wiki/v2/spaces/get_node``
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
    columns one at a time via ``feishu_api`` (``POST
    /open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields``).

    Args:
        app_token: The base's app_token (from ``feishu_api`` ``POST
            /open-apis/bitable/v1/apps`` or a feishu.cn/base/<app_token> URL).
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


async def feishu_bitable_update_field(
    app_token: str,
    table_id: str,
    field_id: str,
    field_name: str = "",
    field_type: int = 0,
    property_json: str = "",
    ui_type: str = "",
    is_primary: bool | None = None,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Change a column's definition — rename it, change its type, edit its options.

    This is how you fix a column that was built wrong **without losing its data**:
    deleting and re-creating the column throws away every value in it. Typical uses:
    rename "备注" to "审批意见", add an option to a 单选 column, turn a 文本 column into
    a 数字 one, switch a 人员 column to allow multiple people.

    Get ``field_id`` from ``feishu_api`` GET .../tables/:table_id/fields. Feishu replaces the whole
    field definition on update, so anything you leave out is read back from the table
    and carried over rather than reset — including the existing ``property`` when the
    type is unchanged. When you *do* pass ``property_json``, it replaces the old
    settings wholesale, so include every option you want to keep.

    Type 19 (查找引用) can't be set, and the table's index (primary) column is limited
    to types 1/2/5/13/15/20/22 — both are refused here rather than by Feishu.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (``feishu_api`` GET /open-apis/bitable/v1/apps/:app_token/tables).
        field_id: The column to change (``feishu_api`` GET .../tables/:table_id/fields).
        field_name: New column name. Omit to keep the current one.
        field_type: New Feishu field-type integer. Omit (0) to keep the current type.
        property_json: Type-specific settings as a JSON object — select options
            ``{"options":[{"name":"高","color":0}]}``, number format
            ``{"formatter":"0.00"}``, date format ``{"date_formatter":"yyyy-MM-dd"}``,
            person multi-select ``{"multiple":true}``. Replaces the old settings.
        ui_type: Optional display variant, e.g. "Progress", "Currency", "Rating".
        is_primary: Optional; True makes this column the table's index (primary) column
            (the previous primary column is demoted automatically). Only allowed for
            index-capable types (1/2/5/13/15/20/22). Omit (None) to leave it unchanged.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who performs the edit (see create_record).
    """
    return _f.dumps_result(
        await _f.update_bitable_field_impl(
            app_token,
            table_id,
            field_id,
            field_name,
            field_type,
            property_json,
            ui_type,
            is_primary,
            user_key,
            identity,
        )
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

    Prefer this over a plain ``feishu_api`` record list whenever you are looking for
    *particular* rows — "张三那几行", "状态是进行中且金额大于一万的", "负责人为空的" —
    and especially when you need a ``record_id`` to feed
    ``feishu_bitable_update_record`` / ``feishu_api`` batch_delete. Feishu's own
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

    Get the exact ``field_name`` spellings from ``feishu_api`` GET .../fields first —
    a wrong column name silently matches nothing.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (``feishu_api`` GET /open-apis/bitable/v1/apps/:app_token/tables).
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


async def feishu_bitable_create_records(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> str:
    """Create MANY records (rows) in a Feishu bitable table in one call.

    Use this instead of a per-row ``feishu_api`` POST .../records in a loop whenever
    you have more than one row to write — filling a fresh ledger, importing a list,
    logging a batch of results. One call rather than N is faster and avoids Feishu's
    per-app rate limit. Writes in batches of 500 (Feishu's per-call cap); a table
    holds at most 20000 records in total.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (``feishu_api`` GET /open-apis/bitable/v1/apps/:app_token/tables).
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

    Find the ``record_id`` with ``feishu_api`` GET .../records (optionally with its
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
        table_id: The table's id (``feishu_api`` GET /open-apis/bitable/v1/apps/:app_token/tables).
        record_id: The row to change (``feishu_api`` GET .../tables/:table_id/records).
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
        table_id: The table's id (``feishu_api`` GET /open-apis/bitable/v1/apps/:app_token/tables).
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


async def feishu_bitable_clear_table(app_token: str, table_id: str, user_key: str = "", identity: str = "") -> str:
    """Delete ALL records (rows) in a Feishu bitable table.

    Pages through every record and batch-deletes them — useful to wipe a table's
    default empty rows (or all data) before writing fresh records. Fields/columns
    are NOT touched (delete columns with ``feishu_api`` DELETE .../fields/:field_id).

    Args:
        app_token: The base's app_token.
        table_id: The table's id (``feishu_api`` GET /open-apis/bitable/v1/apps/:app_token/tables).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see create_record).
    """
    return _f.dumps_result(await _f.clear_bitable_table_impl(app_token, table_id, user_key, identity))
