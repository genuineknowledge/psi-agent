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


async def feishu_bitable_create_record(app_token: str, table_id: str, fields_json: str) -> str:
    """Create one record (row) in a Feishu bitable table.

    Args:
        app_token: The base's app_token.
        table_id: The table's id (from ``feishu_bitable_list_tables``).
        fields_json: A JSON object string mapping column names to values, e.g.
            '{"新人":"张三","Mentor":"李四","反馈内容":"进步明显","评分":4}'.
            Column names must match the table's fields.
    """
    return _f.dumps_result(await _f.create_bitable_record_impl(app_token, table_id, fields_json))
