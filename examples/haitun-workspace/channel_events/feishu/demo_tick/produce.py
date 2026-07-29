"""Synthetic producer template for Feishu Channel.

``async def produce(ctx)`` is started by Channel's unified runner. Use
``await ctx.emit(envelope)`` to POST Session ``/events``. Channel cancel
stops this coroutine.

Env (demo only):
  HAITUN_CHANNEL_EVENTS_DEMO=1          — emit one demo envelope then idle
  HAITUN_CHANNEL_EVENTS_DEMO_OPEN_ID=…  — optional routing.open_id
"""

from __future__ import annotations

import os
from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    """Long-running producer; cancelled when Feishu Channel shuts down."""
    demo = os.environ.get("HAITUN_CHANNEL_EVENTS_DEMO", "").strip() == "1"
    if not demo:
        # Stay alive so the runner keeps the slot; real producers loop/poll here.
        await anyio.sleep_forever()
        return

    await anyio.sleep(0.5)
    open_id = os.environ.get("HAITUN_CHANNEL_EVENTS_DEMO_OPEN_ID", "").strip()
    envelope: dict[str, Any] = {
        "payload": {"reason": "demo_tick", "hint": "copy this dir for real synthetics"},
        "idempotency_key": "feishu.synthetic.demo_tick:once",
    }
    if open_id:
        envelope["routing"] = {"open_id": open_id}
    await ctx.emit(envelope)
    await anyio.sleep_forever()
