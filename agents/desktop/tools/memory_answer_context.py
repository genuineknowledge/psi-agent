from __future__ import annotations

import json
from dataclasses import asdict

from _fusion_memory.runtime import get_runtime


async def memory_answer_context(query: str, limit: int = 12, max_chars: int = 6000) -> str:
    """Build bounded answer context from raw workspace evidence only."""
    runtime = await get_runtime()
    result = await runtime.answer_context(query, max(1, min(limit, 50)), max(256, min(max_chars, 20_000)))
    return json.dumps(
        {
            "ok": runtime.enabled,
            "disabled": not runtime.enabled,
            "query": result.query,
            "evidence": [asdict(hit) for hit in result.evidence],
            "rendered": result.rendered,
        },
        ensure_ascii=False,
    )
