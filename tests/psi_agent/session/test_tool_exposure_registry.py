"""Exposure against a real ``ToolRegistry``: attribution, manifests, and dispatch.

The unit file (``test_tool_exposure.py``) works on hand-built mappings, so it
cannot show that a *loaded* registry answers "whose tool is this" correctly, nor
that a tool the array omitted is still callable. Both need the real loader over
real dirs, which is what this file uses — tool files written to ``tmp_path``, then
``ToolRegistry.load`` / ``load_layers`` over them.

The dispatch criteria here call ``registry.get(name)`` and **await the returned
callable**. Resolving the name is not the claim; the claim is that the tool runs,
and a ``get()`` that returned something unusable would satisfy a weaker
assertion while failing the property production depends on.
"""

from __future__ import annotations

from pathlib import Path

from psi_agent.session.tool_defs import build_tool_defs
from psi_agent.session.tool_exposure import (
    MANIFEST_NAME,
    ExposureTier,
    read_manifest,
    select_exposed,
)
from psi_agent.session.tool_layers import Layer
from psi_agent.session.tool_registry import ToolFunction, ToolRegistry


def _tool_file(directory: Path, name: str, *, returns: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(
        f'''
async def {name}() -> str:
    """A loadable tool named {name}."""
    return "{returns}"
''',
        encoding="utf-8",
    )


def _exposed(registry: ToolRegistry, tier: ExposureTier) -> dict[str, ToolFunction]:
    return select_exposed(
        registry.tools,
        tier=tier,
        layer_of=registry.layer_of_tool,
        manifests=registry.exposure_manifests,
    )


# ── the registry reports what exposure needs ─────────────────────────────────


async def test_single_dir_load_attributes_every_tool_to_one_layer(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    _tool_file(tools_dir, "alpha", returns="a")
    _tool_file(tools_dir, "beta", returns="b")

    registry = await ToolRegistry.load(tools_dir)

    assert set(registry.tools) == {"alpha", "beta"}
    assert len(set(registry.layer_of_tool.values())) == 1
    assert set(registry.layer_of_tool) == {"alpha", "beta"}


async def test_layered_load_attributes_each_tool_to_its_own_layer(tmp_path: Path) -> None:
    """Exposure is decided per layer, so a wrong attribution silently applies the
    wrong layer's manifest to a tool."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _tool_file(official, "shared_tool", returns="official")
    _tool_file(personal, "my_tool", returns="personal")

    registry = await ToolRegistry.load_layers(
        [
            Layer(layer_id="official", tools_dir=official, priority=0),
            Layer(layer_id="personal", tools_dir=personal, priority=10),
        ]
    )

    assert registry.layer_of_tool == {"shared_tool": "official", "my_tool": "personal"}


async def test_a_shadowed_name_is_attributed_to_the_layer_that_wins(tmp_path: Path) -> None:
    """``tools`` and ``get()`` are last-wins, so attribution has to agree with them:
    deciding exposure against a definition the model will never be handed would
    apply the losing layer's manifest."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _tool_file(official, "same_name", returns="from_official")
    _tool_file(personal, "same_name", returns="from_personal")

    registry = await ToolRegistry.load_layers(
        [
            Layer(layer_id="official", tools_dir=official, priority=0),
            Layer(layer_id="personal", tools_dir=personal, priority=10),
        ]
    )

    assert registry.layer_of_tool["same_name"] == "personal"
    winner = registry.get("same_name")
    assert winner is not None
    assert await winner() == "from_personal"


async def test_manifest_is_read_from_each_layers_tools_dir(tmp_path: Path) -> None:
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _tool_file(official, "declared_tool", returns="d")
    _tool_file(official, "undeclared_tool", returns="u")
    _tool_file(personal, "my_tool", returns="m")
    (official / MANIFEST_NAME).write_text("declared_tool\n", encoding="utf-8")

    registry = await ToolRegistry.load_layers(
        [
            Layer(layer_id="official", tools_dir=official, priority=0),
            Layer(layer_id="personal", tools_dir=personal, priority=10),
        ]
    )

    assert registry.exposure_manifests == {"official": frozenset({"declared_tool"}), "personal": None}


async def test_a_dir_without_a_manifest_reports_none(tmp_path: Path) -> None:
    """``None`` is what makes "declared nothing" and "declared none" distinguishable."""
    tools_dir = tmp_path / "tools"
    _tool_file(tools_dir, "alpha", returns="a")
    assert await read_manifest(tools_dir) is None


async def test_the_manifest_file_is_not_loaded_as_a_tool(tmp_path: Path) -> None:
    """It sits in the tools dir on purpose (shipped with the tools it names), so
    the loader must ignore it rather than trip over it."""
    tools_dir = tmp_path / "tools"
    _tool_file(tools_dir, "alpha", returns="a")
    (tools_dir / MANIFEST_NAME).write_text("alpha\n", encoding="utf-8")

    registry = await ToolRegistry.load(tools_dir)
    assert set(registry.tools) == {"alpha"}


# ── discovery, not capability ────────────────────────────────────────────────


async def test_a_tool_left_out_of_the_array_is_still_dispatchable(tmp_path: Path) -> None:
    """The property the whole narrowing rests on, asserted end to end: the omitted
    tool is absent from what the model is handed, and *runs* when dispatched."""
    tools_dir = tmp_path / "tools"
    _tool_file(tools_dir, "declared_tool", returns="declared_ran")
    _tool_file(tools_dir, "hidden_tool", returns="hidden_ran")
    (tools_dir / MANIFEST_NAME).write_text("declared_tool\n", encoding="utf-8")

    registry = await ToolRegistry.load(tools_dir)
    exposed = _exposed(registry, ExposureTier.LAYERED)
    names_in_array = {d["function"]["name"] for d in build_tool_defs(exposed)}

    assert names_in_array == {"declared_tool"}
    hidden = registry.get("hidden_tool")
    assert hidden is not None, "narrowing turned a discovery loss into a capability loss"
    assert await hidden() == "hidden_ran"


async def test_get_stays_total_over_the_registry_under_every_gear(tmp_path: Path) -> None:
    """``get()`` must never return ``None`` for a registered name, whatever the gear.

    Parametrising over the gears is the point: a gear that narrowed the registry
    itself (rather than the array) would pass the criterion above under one gear
    and fail here.
    """
    tools_dir = tmp_path / "tools"
    for i in range(4):
        _tool_file(tools_dir, f"tool_{i}", returns=f"ran_{i}")
    (tools_dir / MANIFEST_NAME).write_text("tool_0\n", encoding="utf-8")

    registry = await ToolRegistry.load(tools_dir)
    all_names = set(registry.tools)
    assert len(all_names) == 4

    for tier in ExposureTier:
        _exposed(registry, tier)
        for name in all_names:
            func = registry.get(name)
            assert func is not None, f"{name} unreachable under {tier.value}"


async def test_a_cross_layer_hidden_tool_is_still_dispatchable(tmp_path: Path) -> None:
    """Same property with the layers real: the shared root's undeclared tool stays
    callable while the user's own layer keeps its full surface."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _tool_file(official, "declared_tool", returns="d")
    _tool_file(official, "hidden_tool", returns="hidden_ran")
    _tool_file(personal, "my_tool", returns="m")
    (official / MANIFEST_NAME).write_text("declared_tool\n", encoding="utf-8")

    registry = await ToolRegistry.load_layers(
        [
            Layer(layer_id="official", tools_dir=official, priority=0),
            Layer(layer_id="personal", tools_dir=personal, priority=10),
        ]
    )

    exposed = set(_exposed(registry, ExposureTier.LAYERED))
    assert exposed == {"declared_tool", "my_tool"}
    hidden = registry.get("hidden_tool")
    assert hidden is not None
    assert await hidden() == "hidden_ran"


# ── the gears, on a loaded registry ──────────────────────────────────────────


async def test_each_gear_exposes_a_different_subset_of_a_real_registry(tmp_path: Path) -> None:
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _tool_file(official, "declared_tool", returns="d")
    _tool_file(official, "hidden_tool", returns="h")
    _tool_file(personal, "my_tool", returns="m")
    (official / MANIFEST_NAME).write_text("declared_tool\n", encoding="utf-8")

    registry = await ToolRegistry.load_layers(
        [
            Layer(layer_id="official", tools_dir=official, priority=0),
            Layer(layer_id="personal", tools_dir=personal, priority=10),
        ]
    )

    assert set(_exposed(registry, ExposureTier.OFF)) == {"declared_tool", "hidden_tool", "my_tool"}
    assert set(_exposed(registry, ExposureTier.LAYERED)) == {"declared_tool", "my_tool"}
    assert set(_exposed(registry, ExposureTier.DECLARED)) == {"declared_tool"}


async def test_refresh_keeps_layer_attribution(tmp_path: Path) -> None:
    """``refresh`` re-reads the top layer every turn; an entry that came back
    without its layer would fall through to "undeclared" and quietly widen the
    array on the next Session."""
    tools_dir = tmp_path / "tools"
    _tool_file(tools_dir, "alpha", returns="a")
    (tools_dir / MANIFEST_NAME).write_text("alpha\n", encoding="utf-8")

    registry = await ToolRegistry.load(tools_dir)
    before = dict(registry.layer_of_tool)

    _tool_file(tools_dir, "beta", returns="b")
    await registry.refresh()

    assert before["alpha"] == registry.layer_of_tool["alpha"]
    assert registry.layer_of_tool["beta"] == before["alpha"]
    assert set(_exposed(registry, ExposureTier.LAYERED)) == {"alpha"}
