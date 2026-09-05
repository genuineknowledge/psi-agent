"""Criteria for the deploy-only M2 tool gate (tmpfix-20260902).

These guard the property production actually depends on: the ``tools`` array
shrinks, while the registry the dispatcher resolves against does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from psi_agent.session.tool_defs import (
    TMPFIX_M2_CORE_TOOLS,
    build_tool_defs,
    tmpfix_m2_gate,
)


@dataclass
class _Tool:
    name: str
    description: str = "d"
    parameters: dict[str, Any] = field(default_factory=dict)


def _registry(*names: str) -> dict[str, _Tool]:
    return {n: _Tool(name=n) for n in names}


def test_gate_drops_noncore_and_keeps_core() -> None:
    reg = _registry("bash", "read", "some_obscure_tool", "another_unused_one")
    gated = tmpfix_m2_gate(reg)
    assert set(gated) == {"bash", "read"}


def test_gate_shrinks_the_rendered_array() -> None:
    """The array is what costs tokens, so assert on the rendered defs."""
    reg = _registry("bash", "read", "noncore_a", "noncore_b", "noncore_c")
    full = build_tool_defs(reg)
    gated = build_tool_defs(tmpfix_m2_gate(reg))
    assert len(full) == 5
    assert len(gated) == 2
    assert {d["function"]["name"] for d in gated} == {"bash", "read"}


def test_gate_passes_through_when_no_core_tool_present() -> None:
    """The async-load window: gating an unfilled registry would freeze it empty."""
    reg = _registry("noncore_a", "noncore_b")
    assert set(tmpfix_m2_gate(reg)) == {"noncore_a", "noncore_b"}


def test_gate_passes_through_empty_registry() -> None:
    assert tmpfix_m2_gate({}) == {}


def test_gate_does_not_mutate_the_registry() -> None:
    """Dispatch resolves against this mapping; narrowing it would break calls."""
    reg = _registry("bash", "noncore_a")
    tmpfix_m2_gate(reg)
    assert set(reg) == {"bash", "noncore_a"}


def test_core_set_covers_the_dispatch_escape_hatch() -> None:
    """``tool_search`` must stay exposed, or omitted tools become undiscoverable."""
    assert "tool_search" in TMPFIX_M2_CORE_TOOLS
    assert "tool_describe" in TMPFIX_M2_CORE_TOOLS
