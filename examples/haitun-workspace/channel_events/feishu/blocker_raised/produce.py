"""Synthetic interface: ``haitun.blocker.raised`` (idle until wired).

Emit example::

    await ctx.emit({
        "payload": {
            "blocker_id": "b1",
            "summary": "报销单缺发票",
            "owner_open_id": "ou_finance",
            "reporter_open_id": "ou_staff",
            "severity": "med",
            "context_ref": "approval:…",
        },
        "routing": {"open_id": "ou_finance"},
        "idempotency_key": "haitun:blocker.raised:b1",
    })
"""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    _ = ctx
    await anyio.sleep_forever()
