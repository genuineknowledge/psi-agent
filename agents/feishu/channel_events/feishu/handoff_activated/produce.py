"""Synthetic interface: `haitun.handoff.activated` (idle until wired)."""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    _ = ctx
    await anyio.sleep_forever()
