"""Read positive-negative ledger records from a Feishu private-chat request."""

# ruff: noqa: E402, RUF001

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from loguru import logger

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import reader, runtime


async def positive_negative_case_read(
    query_json: str = "",
    user_key: str = "",
) -> str:
    """Read one page of positive-negative records from the configured Feishu table.

    Args:
        query_json: JSON object containing optional filters (record id,
            subject/reporter identities, nature, category, keyword, dates).
        user_key: Trusted Feishu sender identity.

    Returns:
        JSON with readable Chinese record fields and a natural-language read
        status.  Full-ledger statistics belong to the analyze tool; this tool
        reads a single page only and never returns a pagination cursor to the
        chat model.
    """
    try:
        adapter = runtime.configured_read_table_adapter()
        parsed = reader.parse_query(
            query_json,
            page_size=100,
            page_token="",
            view_id=runtime.configured_read_view_id(),
        )
        query = replace(parsed, page_size=100, page_token="")
        actual_names = await reader.list_table_field_names(*runtime.read_target_coordinates())
        blocker = await reader.reject_unavailable_filters(query, actual_names)
        if blocker is not None:
            return _f.dumps_result(blocker)
        result = await reader.read_records(cast(reader.FeishuLedgerClient, adapter._client), query, user_key)
        result = await reader.public_result_with_names(result)
    except (TypeError, ValueError) as exc:
        logger.warning(f"pnl case_read: query rejected: {type(exc).__name__}: {exc}")
        result = {
            "ok": False,
            "状态": "读取失败",
            "说明": "查询条件无法解析，请调整后重试。",
            "error": str(exc),
        }
    except (OSError, RuntimeError) as exc:
        logger.warning(f"pnl case_read: read failed: {type(exc).__name__}: {exc}")
        result = {
            "ok": False,
            "状态": "读取失败",
            "说明": "暂时无法读取正负面清单，请稍后重试。",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return _f.dumps_result(result)


__all__ = ["positive_negative_case_read"]
