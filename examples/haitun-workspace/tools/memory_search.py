from __future__ import annotations

from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _client import format_context_pack as _format_context_pack, format_error_result as _format_error_result, post_json as _post_json
from _config import CONFIG


async def memory_search(query: str, limit: int = 8) -> str:
    """Search Fusion Memory for raw evidence.

    Args:
        query: The search query.
        limit: Maximum number of evidence items to return.

    Returns:
        A formatted context pack string or an unavailable message.
    """
    try:
        limit = max(1, min(32, int(limit)))
        data = await _post_json(
            CONFIG.base_url,
            "/search",
            {
                "query": query,
                "scope": CONFIG.scope,
                "options": {"limit": limit, "allow_cross_session": CONFIG.allow_cross_session},
            },
            CONFIG.timeout_seconds,
        )
        return _format_context_pack(data, limit=limit)
    except Exception as exc:
        return _format_error_result(exc)
