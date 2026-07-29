"""Unified runner for agent-package ``kind: synthetic`` producers.

Official events are produced by the platform; synthetic events are produced
here: Channel starts one long-lived task per ``produce.py``. Agent authors
only add ``channel_events/<channel>/<slug>/`` — they do not edit this module
for each new event (刻意为之).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._event_defs import ChannelEventDef

ResolveCore = Callable[[str | None], Awaitable[ChannelCore]]


@dataclass(slots=True)
class SyntheticContext:
    """Duck-typed ctx passed to agent ``produce(ctx)``.

    Agent ``produce.py`` should treat this as opaque: use ``event_name`` /
    ``source`` and ``await ctx.emit(envelope)``. No need to import this type.
    """

    event_name: str
    source: str
    _resolve_core: ResolveCore
    _edef: ChannelEventDef

    async def emit(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """POST one Session envelope via ``ChannelCore.post_event``."""
        if not isinstance(envelope, dict):
            raise TypeError("emit() expects a dict envelope")
        env = dict(envelope)
        env.setdefault("schema_version", 1)
        env.setdefault("source", self.source)
        env.setdefault("event", self.event_name)
        env.setdefault("raw_event", f"synthetic:{self.event_name}")
        routing = env.get("routing") if isinstance(env.get("routing"), dict) else {}
        open_id: str | None = None
        if isinstance(routing, dict):
            oid = routing.get("open_id")
            if isinstance(oid, str) and oid.strip():
                open_id = oid.strip()
        core = await self._resolve_core(open_id)
        return await core.post_event(env)


def start_synthetic_producers(
    defs: list[ChannelEventDef],
    *,
    resolve_core: ResolveCore,
    task_group: Any,  # anyio.TaskGroup (ty 不识别的第三方类型)
) -> int:
    """Schedule one task per synthetic def that has ``produce_fn``. Returns count."""
    started = 0
    for edef in defs:
        if edef.kind != "synthetic" or edef.produce_fn is None:
            continue
        task_group.start_soon(_run_one_producer, edef, resolve_core)
        started += 1
        logger.info(f"Started synthetic producer {edef.name!r} path={edef.path}")
    return started


async def _run_one_producer(edef: ChannelEventDef, resolve_core: ResolveCore) -> None:
    """Run ``produce(ctx)`` until cancelled; errors are logged, task ends."""
    assert edef.produce_fn is not None
    ctx = SyntheticContext(
        event_name=edef.name,
        source=edef.source,
        _resolve_core=resolve_core,
        _edef=edef,
    )
    try:
        await edef.produce_fn(ctx)
        logger.info(f"Synthetic producer {edef.name!r} returned (idle exit)")
    except anyio.get_cancelled_exc_class():
        logger.debug(f"Synthetic producer {edef.name!r} cancelled")
        raise
    except Exception as e:
        logger.error(f"Synthetic producer {edef.name!r} crashed: {e!r}")
