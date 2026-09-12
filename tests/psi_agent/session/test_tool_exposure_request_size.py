"""The request-size floor: what narrowing is *for*, measured rather than assumed.

Tool counts are not what the upstream charges for — a schema's size is its
description plus its parameter schema, and those vary by an order of magnitude
between tools. So every criterion here measures **characters of the serialised
``tools`` array**, the same quantity production logs, and compares gears against
each other on one fixed input.

Fixed input, not production traffic: a criterion that reads a live number cannot
fail on a code change (the number moves for a dozen unrelated reasons, and on a
dev machine it is absent entirely). The fixture below is a stand-in for the shape
production has — a few tools a turn actually calls, beside many it does not —
sized so the ratio is stable, and it is *this repo's own* numbers that get
pinned, not the deployment's.

The production figures quoted in ``tool_exposure`` (285566 → 83725 chars, 70.7%)
were measured on the previous, hardcoded gate and are **not** a claim about the
numbers here; nothing in this file reproduces them, and they are not asserted.
What is asserted is the property those figures were evidence for: the default
gear keeps the array a small fraction of the full surface, and stops being green
if that stops being true.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from psi_agent.session.tool_defs import build_tool_defs
from psi_agent.session.tool_exposure import ExposureTier, select_exposed

# Roughly the schema weight of a real tool: a paragraph of description plus a few
# documented parameters. Kept in the same ballpark as the feishu pack's tools so
# the ratios below are not an artefact of toy-sized fixtures.
_DESCRIPTION = (
    "Read a document and return its text content. Handles the paginated shapes the "
    "upstream returns, retries on the transient errors it reports as success, and "
    "trims the boilerplate header that would otherwise dominate short documents."
)


@dataclass
class _Tool:
    name: str
    description: str = _DESCRIPTION
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "What to operate on: an id, a path, or a url."},
                "page_size": {"type": "integer", "description": "How many entries per page; upstream caps this."},
                "user_key": {"type": "string", "description": "Whose credentials to act as; empty for the bot."},
            },
            "required": ["target"],
        }
    )


_HOT = ("bash", "read", "edit", "write", "tool_search", "tool_describe")
_COLD = tuple(f"cold_tool_{i:03d}" for i in range(150))


def _fixture() -> tuple[dict[str, _Tool], dict[str, str], dict[str, frozenset[str] | None]]:
    """One shared root: 156 tools, of which the manifest declares 6."""
    tools = {name: _Tool(name=name) for name in (*_HOT, *_COLD)}
    layer_of = dict.fromkeys(tools, "official")
    manifests: dict[str, frozenset[str] | None] = {"official": frozenset(_HOT)}
    return tools, layer_of, manifests


def _chars(tools: dict[str, _Tool]) -> int:
    """Characters of *tools* rendered as the request's ``tools`` array.

    ``separators`` is pinned because the default ones add a space per delimiter,
    which on a 150-tool array is thousands of characters of pure measurement
    artefact — the number would then move with json defaults rather than with
    exposure.
    """
    return len(json.dumps(build_tool_defs(tools), ensure_ascii=False, separators=(",", ":")))


def _array_chars(tier: ExposureTier) -> int:
    """Characters of the ``tools`` array the fixture yields under *tier*."""
    tools, layer_of, manifests = _fixture()
    return _chars(select_exposed(tools, tier=tier, layer_of=layer_of, manifests=manifests))


def test_default_gear_holds_the_array_under_a_tenth_of_the_full_surface() -> None:
    """The floor this card exists to defend.

    Goes red if exposure widens — a manifest that grows to cover the cold tools, a
    default gear flipped to ``OFF``, or a selection bug that stops narrowing —
    because all three land as characters here. The bound is a ratio rather than an
    absolute so the fixture can gain tools without a rewrite; 10% is clear of the
    measured 3.81% (3840 chars of 100740) with margin on purpose — this is a
    regression floor, not a performance target to tune against.
    """
    full = _array_chars(ExposureTier.OFF)
    narrowed = _array_chars(ExposureTier.LAYERED)
    assert narrowed < full * 0.10, f"exposure widened: {narrowed} chars of {full} ({narrowed / full:.1%})"


def test_one_fat_schema_moves_characters_far_more_than_it_moves_the_count() -> None:
    """Why this file measures characters and not tool counts.

    Exposing one extra tool is +1 either way, but the two quantities disagree by
    two orders of magnitude when that tool's schema is large — so a count-based
    floor would rate a widening that admits a handful of heavyweight tools as
    negligible. Constructed rather than argued: the same widening is measured both
    ways, and the assertion is that they do not agree.
    """
    tools, layer_of, manifests = _fixture()
    fat = _Tool(name="fat_tool", description=_DESCRIPTION * 40)
    tools["fat_tool"] = fat
    layer_of["fat_tool"] = "official"

    base = select_exposed(tools, tier=ExposureTier.LAYERED, layer_of=layer_of, manifests=manifests)
    widened_manifests: dict[str, frozenset[str] | None] = {"official": frozenset({*_HOT, "fat_tool"})}
    widened = select_exposed(tools, tier=ExposureTier.LAYERED, layer_of=layer_of, manifests=widened_manifests)

    count_growth = (len(widened) - len(base)) / len(base)
    char_growth = (_chars(widened) - _chars(base)) / _chars(base)

    # Measured: 6 → 7 tools (+16.7%) but 3840 → 13451 chars (+250.3%), a 15x
    # divergence. The bound is on the *divergence*, not on either number, because
    # that is the claim — an absolute char threshold would go red when the fixture
    # is resized, which is not a regression in anything.
    assert len(widened) - len(base) == 1
    assert char_growth > count_growth * 5, (
        f"count +{count_growth:.1%} vs chars +{char_growth:.1%}: fixture 里的 schema 重量太均匀, 这条判据已量不出差别"
    )


def test_declared_gear_is_not_larger_than_the_default_gear() -> None:
    """``DECLARED`` is the floor; ordering the gears by size is what makes an
    attribution ("volume moved because the gear moved") readable at all."""
    assert _array_chars(ExposureTier.DECLARED) <= _array_chars(ExposureTier.LAYERED)


def test_off_gear_reproduces_the_full_surface_exactly() -> None:
    """The comparison baseline: ``OFF`` must be the un-narrowed array, or every
    ratio above is measured against something smaller than the real cost."""
    tools, _, _ = _fixture()
    assert _array_chars(ExposureTier.OFF) == _chars(tools)


def test_a_cold_tools_schema_is_absent_from_the_narrowed_array_text() -> None:
    """Not just "fewer entries": the omitted tool's schema text must be gone.

    An entry rendered with a stripped description would shrink the count while
    leaving the model a tool it cannot understand, which is a different (and
    worse) change than not exposing it.
    """
    tools, layer_of, manifests = _fixture()
    kept = select_exposed(tools, tier=ExposureTier.LAYERED, layer_of=layer_of, manifests=manifests)
    text = json.dumps(build_tool_defs(kept), ensure_ascii=False)
    assert "cold_tool_000" not in text
    assert '"bash"' in text
    assert _DESCRIPTION in text
