"""Read-only query tool for the versioned positive-negative rule pack."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f  # noqa: E402
from _positive_negative_list.rules import DEFAULT_VERSION, query_rules  # noqa: E402


async def positive_negative_rules(query: str, version: str = DEFAULT_VERSION, limit: int = 8) -> str:
    """Query stable positive-negative rules without changing rule text or policy.

    Args:
        query: Text to match against rule titles, categories, and keywords.
        version: Exact version identifier, defaulting to ``6.0-shadow``.
        limit: Maximum number of deterministic rule fragments to return.
    """
    if not isinstance(query, str) or not query.strip():
        return _f.dumps_result({"ok": False, "error": "query must be a non-empty string"})
    if not isinstance(version, str) or not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 32:
        return _f.dumps_result(
            {"ok": False, "error": "version must be a string and limit must be an integer from 1 to 32"}
        )
    try:
        rules: list[dict[str, Any]] = query_rules(query, version, limit)
    except ValueError as exc:
        return _f.dumps_result({"ok": False, "error": str(exc)})
    return _f.dumps_result({"ok": True, "version": version, "rules": rules, "match_count": len(rules)})
