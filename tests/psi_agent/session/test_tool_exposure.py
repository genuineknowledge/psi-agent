"""Criteria for per-layer tool exposure — the unit layer.

Scope is deliberate: everything here calls ``tool_exposure`` functions directly,
on hand-built inputs. Two neighbours cover what this file cannot and are not
interchangeable with it:

* ``test_tool_exposure_request_size.py`` — the request-size floor, measured on a
  reproducible fixture rather than asserted on tool *counts*, because counts are
  not what the upstream charges for;
* ``test_tool_exposure_registry.py`` — the registry end of it (a real
  ``ToolRegistry`` over real dirs: layer attribution, manifest reading, and that
  ``get()`` still reaches a tool the array left out).

A criterion asserting "dispatch still works" from here would be a claim about a
layer this file never touches, so it lives there instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from psi_agent.session.tool_defs import build_tool_defs
from psi_agent.session.tool_exposure import (
    DISCOVERY_TOOLS,
    EXPOSURE_TIER_ENV,
    ExposureTier,
    parse_manifest_text,
    select_exposed,
    tier_from_env,
)


@dataclass
class _Tool:
    name: str
    description: str = "d"
    parameters: dict[str, Any] = field(default_factory=dict)


def _registry(*names: str) -> dict[str, _Tool]:
    return {n: _Tool(name=n) for n in names}


# ── the gear switch ──────────────────────────────────────────────────────────


def test_unset_env_selects_layered() -> None:
    """The default gear has to be the narrowing one, or a deployment that sets
    nothing silently pays the un-narrowed request size."""
    assert tier_from_env({}) is ExposureTier.LAYERED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("off", ExposureTier.OFF),
        ("declared", ExposureTier.DECLARED),
        ("layered", ExposureTier.LAYERED),
        ("  LAYERED  ", ExposureTier.LAYERED),
        ("Off", ExposureTier.OFF),
    ],
)
def test_env_selects_each_gear_case_insensitively(raw: str, expected: ExposureTier) -> None:
    assert tier_from_env({EXPOSURE_TIER_ENV: raw}) is expected


def test_unknown_gear_falls_back_to_layered_without_raising() -> None:
    """Read on the Session start path: a typo must not make Sessions unstartable."""
    assert tier_from_env({EXPOSURE_TIER_ENV: "aggressive"}) is ExposureTier.LAYERED


def test_empty_gear_value_is_treated_as_unset() -> None:
    assert tier_from_env({EXPOSURE_TIER_ENV: "   "}) is ExposureTier.LAYERED


# ── the manifest ─────────────────────────────────────────────────────────────


def test_manifest_reads_one_name_per_line() -> None:
    assert parse_manifest_text("bash\nread\nwrite\n") == frozenset({"bash", "read", "write"})


def test_manifest_drops_comments_and_blanks() -> None:
    """Comments are how a manifest carries the reason a name is on it."""
    text = "# 头部说明\n\nbash  # 常用\n   \nread\n#整行注释\n"
    assert parse_manifest_text(text) == frozenset({"bash", "read"})


def test_manifest_with_only_comments_declares_nothing() -> None:
    """Distinct from "no manifest": this layer declared, and declared none."""
    assert parse_manifest_text("# nothing yet\n") == frozenset()


# ── selection, per gear ──────────────────────────────────────────────────────

_LAYERS = {"bash": "official", "obscure_a": "official", "obscure_b": "official", "my_tool": "personal"}
_MANIFESTS: dict[str, frozenset[str] | None] = {"official": frozenset({"bash"}), "personal": None}


def _select(tier: ExposureTier) -> set[str]:
    tools = _registry("bash", "obscure_a", "obscure_b", "my_tool")
    return set(select_exposed(tools, tier=tier, layer_of=_LAYERS, manifests=_MANIFESTS))


def test_layered_keeps_declared_names_and_all_of_an_undeclared_layer() -> None:
    """The gear's whole point: the shared root is narrowed to what it declared,
    while the user's own root — which declared nothing — stays whole."""
    assert _select(ExposureTier.LAYERED) == {"bash", "my_tool"}


def test_declared_drops_even_an_undeclared_layers_tools() -> None:
    """The floor gear: no manifest, no exposure."""
    assert _select(ExposureTier.DECLARED) == {"bash"}


def test_off_exposes_everything() -> None:
    """The un-narrowed array — the state the request-size measurement compares to."""
    assert _select(ExposureTier.OFF) == {"bash", "obscure_a", "obscure_b", "my_tool"}


def test_each_gear_gives_a_different_answer() -> None:
    """A switch whose positions coincide cannot attribute anything, which is the
    reason it exists. Pinned so a future default change cannot quietly collapse
    two gears into one."""
    off, declared, layered = _select(ExposureTier.OFF), _select(ExposureTier.DECLARED), _select(ExposureTier.LAYERED)
    assert declared < layered < off


# ── discovery must survive every gear ────────────────────────────────────────


def test_discovery_tools_are_exposed_even_when_no_manifest_names_them() -> None:
    """Narrowing is only safe because an omitted tool is still findable. A layer
    whose manifest forgot ``tool_search`` would convert "not shown" into "not
    reachable", so the kernel adds them back rather than trusting the manifest."""
    tools = _registry("bash", "tool_search", "tool_describe", "obscure")
    layer_of = dict.fromkeys(tools, "official")
    manifests: dict[str, frozenset[str] | None] = {"official": frozenset({"bash"})}
    kept = select_exposed(tools, tier=ExposureTier.DECLARED, layer_of=layer_of, manifests=manifests)
    assert set(kept) == {"bash", "tool_search", "tool_describe"}
    assert set(kept) >= DISCOVERY_TOOLS


def test_discovery_tools_absent_from_the_registry_are_not_invented() -> None:
    """A pack without ``tool_search`` gets no phantom entry in its array — the
    array is rendered from these objects, so a synthesised name would be a tool
    definition with nothing behind it."""
    tools = _registry("bash", "obscure")
    layer_of = dict.fromkeys(tools, "official")
    kept = select_exposed(
        tools,
        tier=ExposureTier.DECLARED,
        layer_of=layer_of,
        manifests={"official": frozenset({"bash"})},
    )
    assert set(kept) == {"bash"}


# ── the shapes that must not narrow ──────────────────────────────────────────


def test_empty_registry_stays_empty() -> None:
    assert select_exposed({}, tier=ExposureTier.LAYERED, layer_of={}, manifests={}) == {}


def test_narrowing_to_nothing_passes_the_registry_through() -> None:
    """The async-load window. Tool roots load in the background, so an early turn
    can hold only tools no manifest names yet — and the array freezes for the
    Session's life, so narrowing here would strand it empty."""
    tools = _registry("early_a", "early_b")
    kept = select_exposed(
        tools,
        tier=ExposureTier.DECLARED,
        layer_of=dict.fromkeys(tools, "official"),
        manifests={"official": frozenset({"not_loaded_yet"})},
    )
    assert set(kept) == {"early_a", "early_b"}


def test_unknown_layer_is_treated_as_undeclared() -> None:
    """A registry assembled by a caller that tracks no layers keeps full exposure
    rather than losing its tools to a lookup miss."""
    tools = _registry("a", "b")
    kept = select_exposed(tools, tier=ExposureTier.LAYERED, layer_of={}, manifests={})
    assert set(kept) == {"a", "b"}


def test_selection_does_not_mutate_the_input() -> None:
    """Dispatch resolves against this same mapping; narrowing it in place would
    turn a discovery decision into a capability loss."""
    tools = _registry("bash", "obscure")
    select_exposed(
        tools,
        tier=ExposureTier.DECLARED,
        layer_of=dict.fromkeys(tools, "official"),
        manifests={"official": frozenset({"bash"})},
    )
    assert set(tools) == {"bash", "obscure"}


def test_a_layer_declaring_an_empty_manifest_exposes_only_discovery() -> None:
    """ "Expose nothing of mine" is a legitimate declaration, and distinct from
    having no manifest — the pass-through above only fires when *nothing* is left."""
    tools = _registry("a", "b", "tool_search")
    kept = select_exposed(
        tools,
        tier=ExposureTier.LAYERED,
        layer_of=dict.fromkeys(tools, "official"),
        manifests={"official": frozenset()},
    )
    assert set(kept) == {"tool_search"}


def test_selected_tools_render_into_the_array_verbatim() -> None:
    """The selection is upstream of ``build_tool_defs``, so what it keeps is what
    the model is handed — asserted here so the two stay composed."""
    tools = _registry("bash", "obscure")
    kept = select_exposed(
        tools,
        tier=ExposureTier.DECLARED,
        layer_of=dict.fromkeys(tools, "official"),
        manifests={"official": frozenset({"bash"})},
    )
    assert {d["function"]["name"] for d in build_tool_defs(kept)} == {"bash"}
