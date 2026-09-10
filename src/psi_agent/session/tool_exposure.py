"""Which tools reach the model's ``tools`` array — decided per content layer.

The array is a per-turn fixed cost and part of the upstream prefix-cache key
(see ``tool_defs``), so its size is a production number, not a detail: with the
feishu pack's full surface in it, one deployment measured 285566 chars per
request against 83725 with a narrowed array — 70.7% of the body was tool
schemas the turn never used.

What narrows is **discovery, not capability**. Dispatch resolves a name through
``ToolRegistry.get()``, which never consults this module, so a tool left out of
the array stays callable the moment ``tool_search`` surfaces its name. That
property is what makes narrowing safe, and it is the one this module must not
break: ``DISCOVERY_TOOLS`` is therefore exposed unconditionally, because an
array that omits tools *and* omits the means of finding them has turned a
discovery loss into a capability loss.

The narrowing used to be a name list living in this kernel — 60-odd haitun tool
names hardcoded beside the generic loader. A list cannot answer the question a
layered deployment asks, which is not "which names are hot" but "whose tools are
these": the official root is shared by every workspace and ships hundreds of
tools, while a user's own root ships a handful that they wrote and expect to see.
So the decision moves to the layer:

* a layer that **declares** an exposure manifest gets exactly what it declared —
  the shared roots do this, and their manifest is content, versioned with the
  tools it names rather than with the kernel;
* a layer that declares **nothing** is exposed in full, which is what an
  ordinary single-root workspace wants: five tools and no ceremony.

``ExposureTier`` is the gear, read from the environment on every Session start so
a deployment can change it without a rebuild. That matters for attribution more
than for convenience: if request volume or tool behaviour moves after a release,
telling "the new narrowing did it" from "something else did it" is one variable
away, and the fallback is a restart rather than a rollback window.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

import anyio
from loguru import logger

# The file a content root declares its exposed surface in, read from the root's
# tools dir.  Next to the tools it names, so the two move together in one commit
# — a manifest kept in the kernel (or in a deploy config) is a second place to
# remember, and the M2 list it replaces showed what that costs: tools shipped in
# 2026-09 were invisible for days because the list was edited later than they
# were.  The loader only ever globs ``*.py``, so a text file here is inert.
MANIFEST_NAME = "EXPOSED.txt"

# The escape hatch out of a narrowed array.  Kernel-level rather than per-layer
# because it is the *mechanism* narrowing rests on, not a product choice: these
# are how the model finds a tool that was left out, so a manifest that forgot
# them would silently convert every omitted tool into an unreachable one.
DISCOVERY_TOOLS = frozenset({"tool_search", "tool_describe"})

# The env var holding the gear.  Env, not a CLI flag: Sessions are spawned by
# ``SessionManager``, by the Gateway, and by ``psi-agent session`` directly, so a
# flag would have to be threaded through three callers, while the gear is a
# property of the deployment — the same reasoning as ``PSI_CONTENT_ROOTS``.
EXPOSURE_TIER_ENV = "PSI_TOOL_EXPOSURE"


class ExposureTier(StrEnum):
    """How much of the registry reaches the ``tools`` array.

    ``LAYERED`` is the default because it is the only gear whose answer depends
    on the layer rather than on a global switch, and because it is the one that
    holds request volume where the narrowed measurement put it.

    ``DECLARED`` and ``OFF`` are the two ends, kept as gears so a volume or
    behaviour change after a release can be attributed by moving one variable:
    ``OFF`` is the un-narrowed array (what the 285566-char measurement was),
    ``DECLARED`` is the floor, manifests and nothing else.
    """

    OFF = "off"
    """No narrowing: every registered tool is in the array."""

    DECLARED = "declared"
    """Only manifest-declared names. A layer without a manifest contributes none."""

    LAYERED = "layered"
    """Declared names from layers that declare; the full surface from layers that don't."""


def tier_from_env(env: Mapping[str, str] | None = None) -> ExposureTier:
    """The gear ``PSI_TOOL_EXPOSURE`` selects; ``LAYERED`` when unset.

    An unrecognised value warns and falls back rather than raising: this is read
    on the Session start path, and a typo in a deployment's env must not make
    every Session in the container unstartable.
    """
    source = os.environ if env is None else env
    raw = source.get(EXPOSURE_TIER_ENV, "").strip().lower()
    if not raw:
        return ExposureTier.LAYERED
    try:
        return ExposureTier(raw)
    except ValueError:
        valid = ", ".join(tier.value for tier in ExposureTier)
        logger.warning(f"Unknown {EXPOSURE_TIER_ENV}={raw!r}; using {ExposureTier.LAYERED.value} (valid: {valid})")
        return ExposureTier.LAYERED


def parse_manifest_text(raw: str) -> frozenset[str]:
    """The tool names declared in a manifest's text.

    One name per line; ``#`` starts a comment, so a manifest can carry the reason
    a name is on it next to the name. Blank lines are skipped. Split out from
    ``read_manifest`` because callers that already hold the text — the criteria,
    and anything auditing a pack on disk — should not have to fake a filesystem
    to ask what it declares.
    """
    return frozenset(stripped for line in raw.splitlines() if (stripped := line.split("#", 1)[0].strip()))


async def read_manifest(tools_dir: Path) -> frozenset[str] | None:
    """The names *tools_dir* declares exposed, or ``None`` when it declares nothing.

    ``None`` and ``frozenset()`` are different answers and the caller treats them
    differently: no manifest means "this layer has no opinion" (exposed in full
    under ``LAYERED``), while an empty manifest means "expose nothing of mine",
    which a shared root may legitimately want.

    Unreadable file → ``None``, with a warning: failing open to full exposure
    costs request volume, while failing closed would strip a Session's tools over
    a permissions error.
    """
    path = anyio.Path(str(tools_dir / MANIFEST_NAME))
    try:
        if not await path.is_file():
            return None
        raw = await path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Cannot read exposure manifest {str(path)!r}: {e!r}")
        return None
    names = parse_manifest_text(raw)
    logger.debug(f"Exposure manifest {str(path)!r} declares {len(names)} tool(s)")
    return names


def select_exposed[T](
    tools: Mapping[str, T],
    *,
    tier: ExposureTier,
    layer_of: Mapping[str, str],
    manifests: Mapping[str, frozenset[str] | None],
) -> dict[str, T]:
    """The subset of *tools* that goes into the ``tools`` array.

    *layer_of* maps tool name → the ``layer_id`` it was loaded under, and
    *manifests* maps ``layer_id`` → its declared names (``None`` = undeclared).
    A tool whose layer is not in *manifests* is treated as undeclared, so a
    registry assembled by a caller that tracks no layers keeps full exposure
    rather than losing its tools to a lookup miss.

    Returns *tools* unchanged when narrowing would empty a non-empty registry.
    That is the async-load window: tool roots load in the background, so an early
    turn can see a registry holding only tools no manifest names yet, and the
    array is frozen for the Session's life once assembled (``ToolDefsCache``) —
    narrowing there would strand the Session on an empty array for as long as it
    lived. The input is never mutated; dispatch resolves against that same
    mapping.
    """
    if tier is ExposureTier.OFF:
        return dict(tools)

    kept: dict[str, T] = {}
    for name, tool in tools.items():
        declared = manifests.get(layer_of.get(name, ""))
        if declared is None:
            # Undeclared layer: full under LAYERED, nothing under DECLARED.
            if tier is ExposureTier.LAYERED:
                kept[name] = tool
        elif name in declared:
            kept[name] = tool

    for name in DISCOVERY_TOOLS:
        if name in tools:
            kept[name] = tools[name]

    if not kept:
        return dict(tools)
    return kept
