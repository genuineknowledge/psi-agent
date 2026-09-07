"""Analyze positive-negative ledger records from a private-chat request."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import analyzer, reader, runtime
from _positive_negative_list.models import LedgerRecord


async def positive_negative_case_analyze(
    query_json: str = "",
    records_json: str = "",
    focus: str = "",
    user_key: str = "",
) -> str:
    """Aggregate ledger records without writing scores, performance, or table data.

    Args:
        query_json: Optional JSON filters used when reading the configured table.
        records_json: Optional inline JSON array to analyze instead of reading.
        focus: Human-readable focus label retained in the result.
        user_key: Trusted Feishu sender identity used for table reads.

    Returns:
        JSON containing a Chinese user-facing summary.  Pagination cursors,
        storage field names, rule IDs, and other implementation metadata stay
        inside this tool and are never returned to the chat model.
    """
    try:
        if records_json.strip():
            raw = json.loads(records_json)
            if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
                raise ValueError("records_json must be a JSON array of record objects")
            records = [LedgerRecord.from_mapping(item) for item in raw]
        else:
            query = reader.parse_query(query_json)
            adapter = runtime.configured_read_table_adapter()
            records = []
            seen_tokens: set[str] = set()
            while True:
                result = await adapter.list_records(query, user_key)
                if not result.get("ok", True):
                    return _f.dumps_result(dict(result))
                raw_records = result.get("records", [])
                records.extend(LedgerRecord.from_mapping(item) for item in raw_records if isinstance(item, dict))
                if not result.get("has_more"):
                    break
                next_token = str(result.get("page_token") or "").strip()
                if not next_token or next_token in seen_tokens or next_token == query.page_token:
                    return _f.dumps_result({"ok": False, "error": "table pagination cursor did not advance"})
                seen_tokens.add(next_token)
                query = replace(query, page_token=next_token)
        result = {"ok": True, **analyzer.user_summary(records, focus)}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "error": str(exc)}
    except (OSError, RuntimeError) as exc:
        result = {"ok": False, "status": "table_read_failed", "error": f"{type(exc).__name__}: {exc}"}
    return _f.dumps_result(result)


__all__ = ["positive_negative_case_analyze"]
