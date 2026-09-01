"""Synthetic interface: ``haitun.goal.progress`` (idle until wired).

Emit example::

    await ctx.emit({
        "payload": {
            "goal_slug": "ship-payroll-v2",
            "progress": 60,
            "status": "active",
            "note": "考勤导出已通",
            "actor_open_id": "ou_…",
        },
        "routing": {"open_id": "ou_…"},
        "idempotency_key": "haitun:goal.progress:ship-payroll-v2:60",
    })
"""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    _ = ctx
    await anyio.sleep_forever()
