"""Synthetic interface: ``haitun.deliverable.ready`` (idle until wired).

Emit example::

    await ctx.emit({
        "payload": {
            "name": "考勤汇总.xlsx",
            "path": "D:/…/考勤汇总.xlsx",
            "session_id": "feishu-ou_…",
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "actor_open_id": "ou_…",
        },
        "routing": {"open_id": "ou_…"},
        "idempotency_key": "haitun:deliverable.ready:考勤汇总.xlsx",
    })
"""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    _ = ctx
    await anyio.sleep_forever()
