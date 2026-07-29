"""Load Channel event definitions from the agent package.

Layout (per channel name, e.g. ``feishu``)::

    {agent}/channel_events/<channel>/
        <event_dir>/
            EVENT.yaml   # name, source, platform_event?, kind, …
            map.py       # required for kind=platform_map: map_event(raw) -> list[dict]
            produce.py   # required for kind=synthetic: async produce(ctx) -> None

Session only receives envelopes via ``POST /events``. Business event
registry lives here (agent package), not in ``session/event_protocol``.

Adding a new event ≈ adding a tool: drop a directory under
``channel_events/<channel>/``, implement ``map.py`` or ``produce.py``,
restart Channel. Do **not** edit ``src/psi_agent/channel`` for each event
once the Feishu (or other) Channel runner is wired (刻意为之).
"""

from __future__ import annotations

import hashlib
import inspect
import sys
import types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import anyio
import yaml
from loguru import logger

MapEventFn = Callable[[dict[str, Any]], list[dict[str, Any]]]
# produce(ctx) — ctx is SyntheticContext (duck-typed); kept Any to avoid cycle.
ProduceFn = Callable[[Any], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChannelEventDef:
    """One agent-package channel event definition."""

    dir_name: str
    name: str
    source: str
    kind: str  # platform_map | synthetic
    platform_event: str
    description: str
    map_fn: MapEventFn | None
    produce_fn: ProduceFn | None
    path: Path


async def load_channel_event_defs(agent_root: Path, channel: str) -> list[ChannelEventDef]:
    """Load ``channel_events/<channel>/*/EVENT.yaml`` (+ map.py / produce.py)."""
    root = anyio.Path(str(agent_root / "channel_events" / channel))
    try:
        if not await root.is_dir():
            logger.debug(f"No channel_events for {channel!r} under {agent_root}")
            return []
    except Exception as e:
        logger.warning(f"Cannot access channel_events/{channel}: {e!r}")
        return []

    defs: list[ChannelEventDef] = []
    async for entry in root.iterdir():
        try:
            if not await entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            yaml_path = entry / "EVENT.yaml"
            if not await yaml_path.is_file():
                yaml_path = entry / "EVENT.yml"
            if not await yaml_path.is_file():
                logger.warning(f"Skip {entry}: no EVENT.yaml")
                continue
            text = await yaml_path.read_text(encoding="utf-8")
            header = yaml.safe_load(text) or {}
            if not isinstance(header, dict):
                logger.warning(f"Skip {yaml_path}: YAML root must be a mapping")
                continue
            name = str(header.get("name") or entry.name).strip()
            source = str(header.get("source") or channel).strip().casefold()
            kind = str(header.get("kind") or "platform_map").strip().casefold()
            platform_event = str(header.get("platform_event") or "").strip()
            description = str(header.get("description") or "").strip()
            map_fn: MapEventFn | None = None
            produce_fn: ProduceFn | None = None
            map_file = entry / "map.py"
            produce_file = entry / "produce.py"
            if kind == "platform_map":
                if not platform_event:
                    logger.error(f"{entry}: platform_map requires platform_event")
                    continue
                if not await map_file.is_file():
                    logger.error(f"{entry}: platform_map requires map.py")
                    continue
                map_fn = await _load_map_fn(Path(str(map_file)), name)
                if map_fn is None:
                    continue
            elif kind == "synthetic":
                if not await produce_file.is_file():
                    logger.error(f"{entry}: synthetic requires produce.py")
                    continue
                produce_fn = await _load_produce_fn(Path(str(produce_file)), name)
                if produce_fn is None:
                    continue
            else:
                logger.warning(f"{entry}: unknown kind {kind!r}; skipping")
                continue
            defs.append(
                ChannelEventDef(
                    dir_name=entry.name,
                    name=name,
                    source=source,
                    kind=kind,
                    platform_event=platform_event,
                    description=description,
                    map_fn=map_fn,
                    produce_fn=produce_fn,
                    path=Path(str(entry)),
                )
            )
            logger.info(
                f"channel_events/{channel}/{entry.name}: name={name!r} kind={kind!r} platform_event={platform_event!r}"
            )
        except Exception as e:
            logger.error(f"Failed to load channel event from {entry!r}: {e!r}")
    defs.sort(key=lambda d: d.name)
    return defs


async def _load_map_fn(map_path: Path, event_name: str) -> MapEventFn | None:
    """``compile``+``exec`` map.py → ``map_event`` callable (same idea as tools)."""
    module = await _exec_py_module(map_path, event_name, "map")
    if module is None:
        return None
    fn = getattr(module, "map_event", None)
    if not callable(fn):
        logger.error(f"{map_path}: must define map_event(raw) -> list[dict]")
        return None
    return cast(MapEventFn, fn)


async def _load_produce_fn(produce_path: Path, event_name: str) -> ProduceFn | None:
    """``compile``+``exec`` produce.py → async ``produce(ctx)``."""
    module = await _exec_py_module(produce_path, event_name, "produce")
    if module is None:
        return None
    fn = getattr(module, "produce", None)
    if not inspect.iscoroutinefunction(fn):
        logger.error(f"{produce_path}: must define async produce(ctx) -> None")
        return None
    return cast(ProduceFn, fn)


async def _exec_py_module(path: Path, event_name: str, kind: str) -> types.ModuleType | None:
    try:
        source = await anyio.Path(str(path)).read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"Cannot read {path}: {e!r}")
        return None
    file_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    mod_name = f"psi_channel_event_{kind}_{event_name}_{file_hash}"
    try:
        compiled = compile(source, str(path), "exec")
        module = types.ModuleType(mod_name)
        module.__file__ = str(path)
        sys.modules[mod_name] = module
        exec(compiled, module.__dict__)
    except Exception as e:
        sys.modules.pop(mod_name, None)
        logger.error(f"Failed to exec {path}: {e!r}")
        return None
    return module
