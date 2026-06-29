from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _client import format_context_pack as _format_context_pack
from _client import post_json as _post_json
from _config import CONFIG

UNAVAILABLE_MESSAGE = (
    '{"ok": false, "message": "Fusion Memory is not available. '
    'Continue without memory, then run fusion-memory doctor."}'
)


async def memory_answer_context(query: str) -> str:
    """Retrieve a query-grounded context pack from Fusion Memory.

    Args:
        query: The question or topic to retrieve context for.

    Returns:
        A formatted context pack string or an unavailable message.
    """
    try:
        data = await _post_json(
            CONFIG.base_url,
            "/answer-context",
            {
                "query": query,
                "scope": CONFIG.scope,
                "budget": {"limit": 8, "allow_cross_session": CONFIG.allow_cross_session},
            },
            CONFIG.timeout_seconds,
        )
        return _format_context_pack(data)
    except Exception:
        return UNAVAILABLE_MESSAGE
