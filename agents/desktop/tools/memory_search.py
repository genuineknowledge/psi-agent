from __future__ import annotations

import json
from dataclasses import asdict

from _fusion_memory.runtime import get_runtime


async def memory_search(query: str, limit: int = 8) -> str:
    """Search raw visible evidence previously recorded for this workspace."""
    runtime = await get_runtime()
    hits = await runtime.search(query, max(1, min(limit, 50)))
    return json.dumps(
        {"ok": runtime.enabled, "disabled": not runtime.enabled, "evidence": [asdict(hit) for hit in hits]},
        ensure_ascii=False,
    )
