"""Wire agent-package ``channel_events/feishu`` into Feishu WS → Session ``/events``.

- ``kind: platform_map`` — register CustomizedEventProcessor per ``platform_event``
- ``kind: synthetic`` — start unified producer tasks (``produce.py``) in a TaskGroup

After this wiring, new Feishu events are added only under the agent package.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._event_defs import (
    ChannelEventDef,
    channel_events_fingerprint,
    load_channel_event_defs,
)
from psi_agent.channel._event_shapes import describe_shape, non_null_paths, plainify
from psi_agent.channel._synthetic import start_synthetic_producers

# How often to re-scan channel_events/ for new or edited definitions.
_RELOAD_INTERVAL_SECONDS = 5.0

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


def _plainify(value: Any, _depth: int = 0) -> Any:
    """Recursively turn lark SDK model objects into plain dicts/lists.

    lark_channel models (``P2ImChatMemberUserAddedV1Data``, ``UserId``, …) are
    hand-rolled classes with no ``dict()``/``model_dump()``/``to_dict()`` — their
    fields live in ``__dict__``. Without unwrapping them, every P2 payload
    reaches ``map_event`` as ``repr()`` text and no mapper can read a field.

    Shared with the ``channel_event_check`` self-check tool so a probed mapper
    sees byte-for-byte what the live path hands it.
    """
    return plainify(value, _depth)


def _raw_to_dict(raw: Any) -> dict[str, Any]:
    """Best-effort normalize SDK event objects to a plain dict for map_event."""
    if isinstance(raw, dict):
        return {str(k): _plainify(v) for k, v in raw.items()}
    for attr in ("dict", "model_dump", "to_dict"):
        fn = getattr(raw, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return {str(k): _plainify(v) for k, v in out.items()}
            except Exception as e:
                logger.debug(f"Attribute {attr}() failed during raw object normalization: {e!r}")
    # lark events often nest under .event
    nested = getattr(raw, "event", None)
    if nested is not None:
        out = {"event": _plainify(nested)}
        # Keep header/type: mappers need ``header.event_id`` to build an
        # idempotency key that is unique per delivery.
        for attr in ("header", "type", "schema", "ts", "uuid"):
            got = getattr(raw, attr, None)
            if got is not None:
                out[attr] = _plainify(got)
        return out
    plain = _plainify(raw)
    if isinstance(plain, dict):
        return plain
    return {"raw": repr(raw)}


class _LiveEventDefs:
    """Current ``platform_map`` definitions, keyed by ``platform_event``.

    The dispatcher processor for a platform event can only be installed once —
    ``lark`` rebuilds ``_processorMap`` at ``start_background()`` and we skip
    keys another subsystem already owns. So the installed processor never
    closes over a ``ChannelEventDef``; it looks the current one up here at fire
    time. Editing ``map.py`` then takes effect by swapping the entry, with no
    container restart and no second registration.
    """

    def __init__(self) -> None:
        self._by_platform: dict[str, list[ChannelEventDef]] = {}
        self.reloads = 0
        self._changed = anyio.Event()

    def replace(self, defs: list[ChannelEventDef]) -> None:
        grouped: dict[str, list[ChannelEventDef]] = {}
        for edef in defs:
            if edef.kind == "platform_map" and edef.map_fn and edef.platform_event:
                grouped.setdefault(edef.platform_event, []).append(edef)
        self._by_platform = grouped
        self.reloads += 1
        # Wake anyone waiting on a reload, then arm a fresh Event for the next.
        changed, self._changed = self._changed, anyio.Event()
        changed.set()

    async def wait_for_reload(self) -> None:
        """Block until the next ``replace()``. Used by tests and diagnostics."""
        await self._changed.wait()

    def platform_events(self) -> list[str]:
        return sorted(self._by_platform)

    def for_platform(self, platform_event: str) -> list[ChannelEventDef]:
        return list(self._by_platform.get(platform_event, ()))


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
    open ``anyio`` TaskGroup so synthetic producers cancel with Channel — the
    same TaskGroup also hosts the reload watcher that keeps edits to
    ``channel_events/`` live without a restart.
    """
    defs = await load_channel_event_defs(agent_root, "feishu")
    live = _LiveEventDefs()
    live.replace(defs)
    platform_n = _register_platform_map(live, channel, resolve_core, portal_start)
    synthetic_n = 0
    if task_group is not None:
        synthetic_n = start_synthetic_producers(defs, resolve_core=resolve_core, task_group=task_group)
        task_group.start_soon(
            _watch_channel_events,
            live,
            agent_root,
            channel,
            resolve_core,
            portal_start,
        )
    elif any(d.kind == "synthetic" and d.produce_fn for d in defs):
        logger.warning("synthetic channel_events present but no task_group — producers not started")
    return FeishuAgentEventsStats(
        platform_processors=platform_n,
        synthetic_producers=synthetic_n,
    )


async def _watch_channel_events(
    live: _LiveEventDefs,
    agent_root: Path,
    channel: Any,
    resolve_core: Callable[[str | None], Awaitable[ChannelCore]],
    portal_start: Callable[..., Any],
) -> None:
    """Re-load ``channel_events/feishu`` whenever the tree changes.

    Closes the "changed it, cannot verify it" gap: an agent that writes a new
    event directory or fixes a field path in ``map.py`` sees the effect on the
    next delivery instead of needing a restart it cannot perform itself.
    Synthetic producers are **not** restarted here — a running producer task
    cannot be swapped safely, so those still need a Channel restart.
    """
    previous = await channel_events_fingerprint(agent_root, "feishu")
    while True:
        await anyio.sleep(_RELOAD_INTERVAL_SECONDS)
        try:
            current = await channel_events_fingerprint(agent_root, "feishu")
            if current == previous:
                continue
            previous = current
            defs = await load_channel_event_defs(agent_root, "feishu")
            before = set(live.platform_events())
            live.replace(defs)
            after = set(live.platform_events())
            added = _register_platform_map(live, channel, resolve_core, portal_start)
            names = ", ".join(sorted(d.name for d in defs if d.kind == "platform_map")) or "(none)"
            logger.info(
                f"channel_events/feishu reloaded — platform_map: {names}; "
                f"newly registered processors={added}"
                + (f"; dropped {', '.join(sorted(before - after))}" if before - after else "")
            )
        except Exception as e:
            logger.error(f"channel_events/feishu reload failed — {e!r}")


# Marks a processor this module installed, so a reload neither re-wraps its own
# fan-out nor stacks a second one on top of a processor it already owns.
_OURS_ATTR = "_psi_agent_channel_event"


class _AgentEventFanout:
    """Run a built-in dispatcher processor, then fan the delivery out to mappers.

    Some platform events already have an owner in ``_processorMap``: the bot's own
    reply path takes ``p2.im.message.receive_v1`` via ``channel.on("message")``.
    Registering a mapper only on the free key (``p1.*``) puts it on a key the WS
    transport never uses — the mapper loads, probes fine, and still never fires.
    Wrapping keeps the built-in behaviour first and adds the fan-out after.

    ``type()`` must delegate: the dispatcher calls it to pick the deserialization
    target *before* ``do()``, so returning anything else would hand the built-in
    handler an object of the wrong type.
    """

    __slots__ = ("_fanout", "_inner")

    def __init__(self, inner: Any, fanout: Callable[[Any], None]) -> None:
        self._inner = inner
        self._fanout = fanout

    def type(self) -> Any:
        return self._inner.type()

    def do(self, data: Any) -> Any:
        # The built-in handler owns the user-visible behaviour: run it first, and
        # never let a mapper problem turn into a missing reply.
        result = self._inner.do(data)
        try:
            self._fanout(data)
        except Exception as e:
            logger.warning(f"agent event fan-out failed — {e!r}")
        return result


def _is_ours(processor: Any) -> bool:
    """Whether *processor* was installed by this module (plain or fan-out)."""
    return isinstance(processor, _AgentEventFanout) or getattr(processor, _OURS_ATTR, False) is True


def _register_platform_map(
    live: _LiveEventDefs,
    channel: Any,
    resolve_core: Callable[[str | None], Awaitable[ChannelCore]],
    portal_start: Callable[..., Any],
) -> int:
    """Install one dispatcher processor per platform event (idempotent).

    Safe to call repeatedly: keys already carrying a fan-out are skipped, keys with
    a built-in owner get wrapped (see :class:`_AgentEventFanout`), and every
    installed processor resolves its mapper from *live* on each delivery.
    """
    if _CustomizedEventProcessor is None:
        logger.warning("lark_channel CustomizedEventProcessor missing — agent events off")
        return 0

    platform_events = live.platform_events()
    if not platform_events:
        logger.info("No feishu platform_map events under channel_events/feishu")
        return 0

    dispatcher = getattr(channel, "dispatcher", None)
    proc_map = getattr(dispatcher, "_processorMap", None)
    if not isinstance(proc_map, dict):
        logger.warning("agent events unavailable — dispatcher has no _processorMap")
        return 0

    registered = 0
    for platform_event in platform_events:
        for schema in ("p1", "p2"):
            key = f"{schema}.{platform_event}"
            existing = proc_map.get(key)
            if _is_ours(existing):
                logger.debug(f"channel event processor already installed for {key}; skipping")
                continue

            def _on_event(raw: Any, _platform_event: str = platform_event) -> None:
                # Resolve now, not at registration — picks up hot-reloaded mappers.
                edefs = live.for_platform(_platform_event)
                if not edefs:
                    logger.debug(f"no channel_event owns {_platform_event!r} anymore; ignoring")
                    return
                for edef in edefs:
                    try:
                        portal_start(_forward_one, edef, raw, resolve_core)
                    except Exception as e:
                        logger.warning(f"schedule agent event {edef.name!r} failed — {e!r}")

            try:
                if existing is None:
                    processor = _CustomizedEventProcessor(_on_event)
                    with contextlib.suppress(AttributeError):
                        setattr(processor, _OURS_ATTR, True)
                    proc_map[key] = processor
                    logger.info(f"Registered channel event processor → {key}")
                else:
                    # A built-in handler already owns this key (e.g. ``channel.on("message")``
                    # takes ``p2.im.message.receive_v1``). Skipping would leave the mapper on
                    # a key that never sees traffic, so wrap instead: the original still runs,
                    # then the same delivery fans out to channel_events mappers.
                    proc_map[key] = _AgentEventFanout(existing, _on_event)
                    logger.info(f"Wrapped existing processor for fan-out → {key}")
                registered += 1
            except Exception as e:
                logger.warning(f"register {key} failed — {e!r}")
    return registered


def _delivery_id(raw_dict: dict[str, Any]) -> str:
    """Return Feishu's per-delivery id (``header.event_id``, or P1 ``uuid``)."""
    header = raw_dict.get("header")
    if isinstance(header, dict):
        got = header.get("event_id")
        if isinstance(got, str) and got.strip():
            return got.strip()
    got = raw_dict.get("uuid")
    if isinstance(got, str) and got.strip():
        return got.strip()
    return ""


def _event_body(raw_dict: dict[str, Any]) -> Any:
    """The part of the payload a mapper reads fields from."""
    body = raw_dict.get("event")
    return body if isinstance(body, dict) else raw_dict


def _log_empty_mapping(edef: ChannelEventDef, raw_dict: dict[str, Any]) -> None:
    """Explain a mapper that returned no envelopes — otherwise it is silent.

    ``matched=1 fired=[]`` looks identical whether the mapper dropped the event
    or Session deduped it, so print the shape the mapper actually saw and the
    paths that hold values. A wrong field path becomes obvious by comparison.

    A mapper that declares ``filters: true`` in ``EVENT.yaml`` subscribes to a
    broad platform event on purpose and returns ``[]`` for every delivery it
    does not care about (``identity_changed`` ignores avatar/phone edits, which
    are most of ``contact.user.updated_v3``). For those, an empty result is
    normal operation, so it goes to DEBUG with the same detail — WARNING on
    every filtered delivery would be routine noise, and a diagnostic that cries
    wolf stops being read.
    """
    body = _event_body(raw_dict)
    paths = non_null_paths(body)
    shown = ", ".join(paths[:18]) or "(none)"
    seen = f"The mapper saw event{{{describe_shape(body)}}}. Readable paths: {shown}."
    if edef.filters:
        logger.debug(
            f"{edef.name}: map_event returned no envelopes (filters: true — expected for most deliveries). {seen}"
        )
        return
    logger.warning(
        f"{edef.name}: map_event returned no envelopes — event dropped, "
        f"no trigger will fire. {seen} Compare these against the field paths in "
        f"{edef.path / 'map.py'}; the channel_event_check tool replays a sample event. "
        f"If returning [] is intended here, declare `filters: true` in EVENT.yaml."
    )


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
        try:
            envelopes = edef.map_fn(raw_dict)
        except Exception as e:
            body = _event_body(raw_dict)
            logger.error(
                f"{edef.name}: map_event raised {e!r} — event dropped. It was given event{{{describe_shape(body)}}}."
            )
            return
        if not isinstance(envelopes, list):
            logger.error(f"{edef.name}: map_event must return list[dict], got {type(envelopes)!r}")
            return
        if not envelopes:
            _log_empty_mapping(edef, raw_dict)
            return
        delivery_id = _delivery_id(raw_dict)
        for index, env in enumerate(envelopes):
            if not isinstance(env, dict):
                logger.error(f"{edef.name}: envelope is not a dict")
                continue
            # Fill defaults from EVENT.yaml if mapper omitted them.
            env.setdefault("schema_version", 1)
            env.setdefault("source", edef.source)
            env.setdefault("event", edef.name)
            env.setdefault("raw_event", edef.platform_event)
            # Without a key Session cannot dedupe Feishu's own retries; scope it
            # to this delivery so distinct occurrences never collide.
            key = env.get("idempotency_key")
            if (not isinstance(key, str) or not key.strip()) and delivery_id:
                env["idempotency_key"] = f"{edef.name}:{delivery_id}:{index}"
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
