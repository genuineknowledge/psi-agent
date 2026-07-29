"""Wire agent-package ``channel_events/feishu`` into Feishu WS → Session ``/events``.

- ``kind: platform_map`` — register CustomizedEventProcessor per ``platform_event``
- ``kind: synthetic`` — start unified producer tasks (``produce.py``) in a TaskGroup

After this wiring, new Feishu events are added only under the agent package.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._event_defs import ChannelEventDef, load_channel_event_defs
from psi_agent.channel._synthetic import start_synthetic_producers

_CustomizedEventProcessor: Any = None
try:
    from lark_channel.event.custom import CustomizedEventProcessor

    _CustomizedEventProcessor = CustomizedEventProcessor
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True, slots=True)
class FeishuAgentEventsStats:
    """How many Feishu agent-package events were wired at Channel start."""

    platform_processors: int
    synthetic_producers: int


def _raw_to_dict(raw: Any) -> dict[str, Any]:
    """Best-effort normalize SDK event objects to a plain dict for map_event."""
    if isinstance(raw, dict):
        return dict(raw)
    for attr in ("dict", "model_dump", "to_dict"):
        fn = getattr(raw, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return out
            except Exception:
                pass
    # lark events often nest under .event
    nested = getattr(raw, "event", None)
    if isinstance(nested, dict):
        out: dict[str, Any] = {"event": nested}
        header = getattr(raw, "header", None)
        if isinstance(header, dict):
            out["header"] = dict(header)
        elif header is not None:
            header_dict = {
                name: value
                for name in ("event_id", "create_time", "event_type", "tenant_key", "app_id")
                if (value := getattr(header, name, None)) is not None
            }
            if header_dict:
                out["header"] = header_dict
        for name in ("uuid", "ts", "type", "schema"):
            if (value := getattr(raw, name, None)) is not None:
                out[name] = value
        return out
    if nested is not None:
        inner = _raw_to_dict(nested)
        return {"event": inner, "type": getattr(raw, "type", None)}
    return {"raw": repr(raw)}


async def register_feishu_agent_events(
    *,
    channel: Any,
    agent_root: Path,
    resolve_core: Callable[[str | None], Awaitable[ChannelCore]],
    portal_start: Callable[..., Any],
    task_group: Any | None = None,
) -> FeishuAgentEventsStats:
    """Load ``channel_events/feishu``; register platform_map + start synthetics.

    Must run **after** ``start_background()`` (dispatcher rebuild). Pass an
    open ``anyio`` TaskGroup so synthetic producers cancel with Channel.
    """
    defs = await load_channel_event_defs(agent_root, "feishu")
    platform_n = _register_platform_map(defs, channel, resolve_core, portal_start)
    synthetic_n = 0
    if task_group is not None:
        synthetic_n = start_synthetic_producers(defs, resolve_core=resolve_core, task_group=task_group)
    elif any(d.kind == "synthetic" and d.produce_fn for d in defs):
        logger.warning("synthetic channel_events present but no task_group — producers not started")
    return FeishuAgentEventsStats(
        platform_processors=platform_n,
        synthetic_producers=synthetic_n,
    )


def _register_platform_map(
    defs: list[ChannelEventDef],
    channel: Any,
    resolve_core: Callable[[str | None], Awaitable[ChannelCore]],
    portal_start: Callable[..., Any],
) -> int:
    if _CustomizedEventProcessor is None:
        logger.warning("lark_channel CustomizedEventProcessor missing — agent events off")
        return 0

    platform_defs = [d for d in defs if d.kind == "platform_map" and d.map_fn and d.platform_event]
    if not platform_defs:
        logger.info("No feishu platform_map events under channel_events/feishu")
        return 0

    dispatcher = getattr(channel, "dispatcher", None)
    proc_map = getattr(dispatcher, "_processorMap", None)
    if not isinstance(proc_map, dict):
        logger.warning("agent events unavailable — dispatcher has no _processorMap")
        return 0

    registered = 0
    for edef in platform_defs:
        for schema in ("p1", "p2"):
            key = f"{schema}.{edef.platform_event}"
            if key in proc_map:
                logger.debug(f"processor already present for {key}; skipping")
                continue

            def _on_event(raw: Any, _edef: ChannelEventDef = edef) -> None:
                try:
                    portal_start(_forward_one, _edef, raw, resolve_core)
                except Exception as e:
                    logger.warning(f"schedule agent event {_edef.name!r} failed — {e!r}")

            try:
                proc_map[key] = _CustomizedEventProcessor(_on_event)
                registered += 1
                logger.info(f"Registered channel event {edef.name!r} → {key}")
            except Exception as e:
                logger.warning(f"register {key} failed — {e!r}")
    return registered


async def _forward_one(
    edef: ChannelEventDef,
    raw: Any,
    resolve_core: Callable[[str | None], Awaitable[ChannelCore]],
) -> None:
    """Map platform payload → envelope(s) → ``ChannelCore.post_event``."""
    try:
        if edef.map_fn is None:
            return
        raw_dict = _raw_to_dict(raw)
        envelopes = edef.map_fn(raw_dict)
        if not isinstance(envelopes, list):
            logger.error(f"{edef.name}: map_event must return list[dict], got {type(envelopes)!r}")
            return
        for env in envelopes:
            if not isinstance(env, dict):
                logger.error(f"{edef.name}: envelope is not a dict")
                continue
            # Fill defaults from EVENT.yaml if mapper omitted them.
            env.setdefault("schema_version", 1)
            env.setdefault("source", edef.source)
            env.setdefault("event", edef.name)
            env.setdefault("raw_event", edef.platform_event)
            routing = env.get("routing") if isinstance(env.get("routing"), dict) else {}
            open_id = None
            if isinstance(routing, dict):
                oid = routing.get("open_id")
                if isinstance(oid, str) and oid.strip():
                    open_id = oid.strip()
            core = await resolve_core(open_id)
            await core.post_event(env)
    except Exception as e:
        logger.error(f"Unhandled error forwarding channel event {edef.name!r}: {e!r}")
