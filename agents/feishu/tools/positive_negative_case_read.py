"""Read positive-negative ledger records from a Feishu private-chat request."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import reader, runtime


async def positive_negative_case_read(
    query_json: str = "",
    page_size: int = 100,
    page_token: str = "",
    user_key: str = "",
) -> str:
    """Read paginated positive-negative records from the configured Feishu table.

    Args:
        query_json: JSON object containing optional filters and pagination fields.
        page_size: Number of rows to request, from 1 to 500.
        page_token: Cursor returned by a previous page.
        user_key: Trusted Feishu sender identity.

    Returns:
        JSON with readable Chinese record fields and a natural-language read
        status.  The internal pagination cursor is consumed by the tool and is
        not returned to the chat model.
    """
    try:
        adapter = runtime.configured_read_table_adapter()
        query = reader.parse_query(
            query_json,
            page_size=page_size,
            page_token=page_token,
            view_id=runtime.configured_read_view_id(),
        )
        result = await reader.read_records(cast(reader.FeishuLedgerClient, adapter._client), query, user_key)
        result = await reader.public_result_with_names(result)
    except (TypeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    except (OSError, RuntimeError) as exc:
        result = {"ok": False, "status": "table_read_failed", "error": f"{type(exc).__name__}: {exc}"}
    return _f.dumps_result(result)


__all__ = ["positive_negative_case_read"]
