from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import anyio
from loguru import logger

# 刷新「已读 / 接收进度」不需要分钟级新鲜度。每分钟一轮会让每个已注册用户各点火一次
# (线上实测 22 人 → 每分钟 22 次), 每次都要抢一次 Session 锁, 挤占正常对话。
_INTERVAL_SECONDS = 900


async def produce(ctx: Any) -> None:
    while True:
        tick = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")
        try:
            open_ids = await _registered_open_ids()
        except Exception as exc:
            logger.warning(f"Assignment delivery token map read failed: {exc}")
            open_ids = []
        # 把一轮的投递摊到整个周期里, 而不是紧凑循环一次性发完: 后者会让所有人的
        # Session 在同一瞬间抢锁 (thundering herd)。摊开后同一时刻只有一个在跑。
        gap = _INTERVAL_SECONDS / max(len(open_ids), 1)
        for open_id in open_ids:
            try:
                await ctx.emit(
                    {
                        "payload": {"tick": tick},
                        "routing": {"open_id": open_id},
                        "idempotency_key": (f"haitun.assignment.delivery_check:{open_id}:{tick}"),
                    }
                )
            except Exception as exc:
                logger.warning(f"Assignment delivery event emit failed for {open_id}: {exc}")
            # 睡在循环体内即等于走完一个完整周期, 所以循环末尾不再另外 sleep。
            await anyio.sleep(gap)
        if not open_ids:
            # 没有已注册用户时上面一次都没睡, 这里补足一个周期免得空转。
            await anyio.sleep(_INTERVAL_SECONDS)


async def _registered_open_ids() -> list[str]:
    raw_path = os.environ.get("FUSION_MEMORY_TOKEN_MAP_FILE", "").strip()
    if not raw_path:
        return []
    path = anyio.Path(raw_path)
    try:
        raw = await path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except OSError, json.JSONDecodeError:
        return []
    if not isinstance(value, dict):
        return []
    return sorted(
        open_id.strip()
        for open_id, entry in value.items()
        if isinstance(open_id, str)
        and open_id.strip().startswith("ou_")
        and isinstance(entry, dict)
        and isinstance(entry.get("token"), str)
        and entry["token"].strip()
    )
