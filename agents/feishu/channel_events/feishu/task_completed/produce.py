"""Synthetic interface: ``haitun.task.completed`` (idle until a real producer is wired).

Emit example::

    await ctx.emit({
        "payload": {
            "task_id": "t1",
            "title": "提交月度考勤",
            "session_id": "feishu-ou_…",
            "actor_open_id": "ou_…",
            "status": "completed",
        },
        "routing": {"open_id": "ou_…"},
        "idempotency_key": "haitun:task.completed:t1",
    })
"""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    """Placeholder producer — stays registered; does not emit until wired."""
    _ = ctx
    await anyio.sleep_forever()
