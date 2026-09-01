"""Synthetic interface: ``haitun.handoff.needed`` (idle until wired).

Emit example::

    await ctx.emit({
        "payload": {
            "owner_open_id": "ou_owner",
            "requester_open_id": "ou_peer",
            "topic": "Q3 预算对接",
            "reason": "leave",
            "ledger_ref": "bitable://…",
        },
        "routing": {"open_id": "ou_owner"},
        "idempotency_key": "haitun:handoff.needed:ou_owner:Q3",
    })
"""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    _ = ctx
    await anyio.sleep_forever()
