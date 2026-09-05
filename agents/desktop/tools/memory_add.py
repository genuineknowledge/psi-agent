from __future__ import annotations

import json
from dataclasses import asdict

from _fusion_memory.runtime import get_runtime


async def memory_add(source_span_ids: list[str], kind: str = "fact", salience: float = 0.8) -> str:
    """Promote existing raw evidence; free-form text is deliberately unsupported."""
    runtime = await get_runtime()
    item = await runtime.promote(source_span_ids, kind, salience)
    return json.dumps(
        {"ok": item is not None, "disabled": not runtime.enabled, "item": asdict(item) if item else None},
        ensure_ascii=False,
    )
