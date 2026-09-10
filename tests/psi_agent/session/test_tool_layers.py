"""Criteria for the kernel's per-layer private-module isolation (``tool_layers``).

The sibling A1 probes judged a prototype hook that lived in the test file.  These
criteria judge the shipping one: every layer here is opened through
``tool_layers.layers_open`` and every tool file runs through
``tool_layers.executing_tool_file``, the same two entry points
``ToolRegistry.load_layers`` uses.  Nothing in this file reimplements resolution,
so a criterion that passes here says the kernel behaves, not that a local copy
does.

The K-series covers the four pieces of scaffolding A1 needs, the resolution
semantics (asking layer first, priority for fallback only), and the absence of
residue.  ``test_mutations_redden_exactly_their_own_criteria`` is the red-check:
each mutation disables exactly one kernel behaviour by patching the kernel, and
the map from mutation to reddened criteria is an executable assertion, so an
implementation drift that stops a criterion from being load-bearing is caught
rather than passing silently.

Verdicts are read back out of a ``MARKER`` value that reached the tool module.
``sys.modules`` is inspected only by the residue criterion, where the residue
*is* the subject.
"""

from __future__ import annotations

import sys
import textwrap
import types
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

from psi_agent.session import tool_layers, tool_registry
from psi_agent.session.tool_layers import (
    SCOPE_PREFIX,
    Layer,
    LayerImportHook,
    executing_tool_file,
    layers_open,
    scope_package_name,
    scoped_module_name,
)
from psi_agent.session.tool_registry import _tools_dir_on_sys_path

# ── the shipping ladder, as the assembler's knowledge ─────────────────────────

# Gaps of 10 so a tier can be inserted without renumbering.  This is test data on
# purpose: it is what a caller of ``load_layers`` knows about its own layers, and
# the resolution code never consults it.
LAYER_PRIORITY = {"official": 0, "enterprise": 10, "personal": 20}


def make_layer(layer_id: str, tools_dir: Path) -> Layer:
    """A ``Layer`` whose priority comes from ``LAYER_PRIORITY``.

    Every fixture goes through here so no test states a rank inline and drifts
    from the ladder the criteria judge against.  An unknown id fails rather than
    defaulting: a silently-zero priority would put the layer behind official and
    the symptom would look like a resolution bug.
    """
    if layer_id not in LAYER_PRIORITY:
        raise KeyError(f"no priority declared for layer {layer_id!r}; known: {sorted(LAYER_PRIORITY)}")
    return Layer(layer_id, tools_dir, LAYER_PRIORITY[layer_id])


# ── mutations: kernel behaviours switched off from the outside ─────────────────

# Each mutation monkeypatches one method of the shipping hook.  Patching from the
# test rather than shipping ``if MUTATION[...]`` branches in the kernel keeps the
# production path free of test-only switches, and means a mutation cannot pass by
# being wired to a knob nobody reads: it has to actually replace kernel behaviour.


def _mutation_drop_layer_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope names without the layer token → one shared key for every layer."""
    monkeypatch.setattr(tool_layers, "_scope_token", lambda layer_id: "shared")


def _mutation_ignore_asking_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never prefer the asking layer's own file; take the ranked order as-is."""
    monkeypatch.setattr(LayerImportHook, "_search_order", LayerImportHook._ranked_layers)


def _mutation_no_cross_layer_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ever look in the asking layer, so derivation becomes a wall."""

    def only_own(self: LayerImportHook) -> list[Layer]:
        asking = tool_layers._asking_layer.get(None)
        ranked = self._ranked_layers()
        if asking is None:
            return ranked
        return [layer for layer in ranked if layer.layer_id == asking]

    monkeypatch.setattr(LayerImportHook, "_search_order", only_own)


def _mutation_no_module_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-exec a helper on every import instead of handing back the module."""
    monkeypatch.setattr(LayerImportHook, "_cached_module", lambda self, scoped: None)


def _mutation_leak_plain_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never drop the transient plain-name binding — the original hazard, back."""
    monkeypatch.setattr(LayerImportHook, "release_aliases", lambda self: None)


def _mutation_no_scope_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never register ``psi_layer_<token>`` as a package."""
    monkeypatch.setattr(LayerImportHook, "_ensure_scope_package", lambda self, layer_id: None)


def _mutation_ignore_layer_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search in open order instead of priority order."""
    monkeypatch.setattr(LayerImportHook, "_ranked_layers", lambda self: list(self._layers))


def _mutation_reverse_layer_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search least-specific layer first."""
    monkeypatch.setattr(
        LayerImportHook,
        "_ranked_layers",
        lambda self: sorted(self._layers, key=lambda layer: layer.priority),
    )


def _mutation_priority_over_asking_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rank purely by priority, so a higher layer answers for the asking layer.

    Not a scaffolding removal but the *other* answer to "asking layer first or
    highest priority first".  A mutation rather than prose so the question is
    settled by measuring which criteria it breaks.
    """
    monkeypatch.setattr(LayerImportHook, "_search_order", LayerImportHook._ranked_layers)


MUTATIONS = {
    "drop_layer_prefix": _mutation_drop_layer_prefix,
    "ignore_asking_layer": _mutation_ignore_asking_layer,
    "no_cross_layer_fallback": _mutation_no_cross_layer_fallback,
    "no_module_reuse": _mutation_no_module_reuse,
    "leak_plain_aliases": _mutation_leak_plain_aliases,
    "no_scope_package": _mutation_no_scope_package,
    "ignore_layer_priority": _mutation_ignore_layer_priority,
    "reverse_layer_priority": _mutation_reverse_layer_priority,
    "priority_over_asking_layer": _mutation_priority_over_asking_layer,
}


@contextmanager
def mutated(knob: str) -> Iterator[None]:
    """Replace one kernel behaviour for the duration."""
    monkeypatch = pytest.MonkeyPatch()
    MUTATIONS[knob](monkeypatch)
    try:
        yield
    finally:
        monkeypatch.undo()


# ── the loading shell: kernel entry points only ───────────────────────────────


def _exec_layers_together(layers: Sequence[Layer]) -> dict[str, types.ModuleType]:
    """Open every layer at once and exec each layer's public tool files.

    Still enters ``_tools_dir_on_sys_path`` per layer, because that is the world
    the kernel runs in: those dirs are on ``sys.path`` and the stash/restore pair
    is live.  Whether the two mechanisms fight is itself a criterion (K7).

    Returns ``{"<layer_id>/<file stem>": module}``.
    """
    modules: dict[str, types.ModuleType] = {}
    with ExitStack() as stack:
        for layer in layers:
            stack.enter_context(_tools_dir_on_sys_path(layer.tools_dir))
        stack.enter_context(layers_open(layers))
        for layer in layers:
            for py_file in sorted(layer.tools_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                modules[f"{layer.layer_id}/{py_file.stem}"] = _exec_tool_file(py_file, layer)
    return modules


def _exec_tool_file(py_file: Path, layer: Layer) -> types.ModuleType:
    """Compile + exec one public tool file the way the kernel's loader does."""
    module_name = f"psi_kcrit_{layer.layer_id}_{py_file.stem}"
    module = types.ModuleType(module_name)
    module.__file__ = str(py_file)
    sys.modules[module_name] = module
    with executing_tool_file(layer.layer_id):
        exec(compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec"), module.__dict__)
    return module


def _ordered(official: Layer, personal: Layer, order: str) -> list[Layer]:
    """The two layers, in the requested open order."""
    return [official, personal] if order == "official-first" else [personal, official]


BOTH_ORDERS = pytest.mark.parametrize("order", ["official-first", "personal-first"])


# ── layer fixtures on disk ────────────────────────────────────────────────────


def _bare_layer(root: Path, layer_id: str, marker: str) -> Layer:
    """A layer with a bare-name private helper and two tools importing it."""
    tools_dir = root / layer_id / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "_layer_helper.py").write_text(f"MARKER = {marker!r}\nSTATE = []\n", encoding="utf-8")
    (tools_dir / "probe.py").write_text(
        "import _layer_helper as HELPER\nfrom _layer_helper import MARKER as SEEN\n", encoding="utf-8"
    )
    # Second importer in the same layer: K5 compares the two files' bindings.
    (tools_dir / "probe_second.py").write_text(
        "import _layer_helper as HELPER\nSEEN = HELPER.MARKER\n", encoding="utf-8"
    )
    return make_layer(layer_id, tools_dir)


DOTTED_IMPORTERS = {
    "probe": "from _layer_pkg.sub import MARKER as SEEN\n",
    "probe_from_pkg": "from _layer_pkg import sub\n\nSEEN = sub.MARKER\n",
    "probe_import_dotted": "import _layer_pkg.sub\n\nSEEN = _layer_pkg.sub.MARKER\n",
}


def _dotted_layer(root: Path, layer_id: str, marker: str, sole_form: str | None = None) -> Layer:
    """A layer whose helper is a package — the shape ``agents/feishu/tools/_feishu/`` ships.

    The submodule imports its layer's bare-name sibling too (``_feishu/*.py`` all
    do ``import _feishu_impl``), so a nested private import resolving from inside
    an already-scoped module is covered.

    *sole_form* writes only that one importer.  With all three, glob order hides a
    real failure: ``probe.py``'s ``from _layer_pkg.sub import X`` runs first and
    leaves ``sub`` bound as an attribute on the scoped parent, so
    ``probe_from_pkg.py`` reads that attribute and never walks up to
    ``psi_layer_<token>`` — which is why the multi-importer fixture cannot witness
    the scope package's absence even though it names that form as its subject.
    """
    tools_dir = root / layer_id / "tools"
    package = tools_dir / "_layer_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sub.py").write_text("import _layer_core as CORE\n\nMARKER = CORE.WHO + '-sub'\n", encoding="utf-8")
    (tools_dir / "_layer_core.py").write_text(f"WHO = {marker!r}\n", encoding="utf-8")
    wanted = DOTTED_IMPORTERS if sole_form is None else {sole_form: DOTTED_IMPORTERS[sole_form]}
    for name, source in wanted.items():
        (tools_dir / f"{name}.py").write_text(source, encoding="utf-8")
    return make_layer(layer_id, tools_dir)


def _relative_import_layer(root: Path, layer_id: str, marker: str) -> Layer:
    """A layer whose private package uses an in-package relative import.

    The shape ``agents/desktop/tools/_fusion_memory/`` ships: ``__init__.py``
    pulls a submodule in with ``from .journal import ...``.  A relative import
    resolves the current package through the normal machinery, so it needs the
    scope package even though no tool file writes ``from _pkg import sub``.
    """
    tools_dir = root / layer_id / "tools"
    package = tools_dir / "_layer_rel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from .sibling import VALUE as VALUE\n", encoding="utf-8")
    (package / "sibling.py").write_text("import _layer_core as CORE\n\nVALUE = CORE.WHO + '-rel'\n", encoding="utf-8")
    (tools_dir / "_layer_core.py").write_text(f"WHO = {marker!r}\n", encoding="utf-8")
    (tools_dir / "probe.py").write_text("import _layer_rel\n\nSEEN = _layer_rel.VALUE\n", encoding="utf-8")
    return make_layer(layer_id, tools_dir)


def _derivation_layers(root: Path) -> tuple[Layer, Layer]:
    """Official ships a helper the personal layer imports but does not have.

    The official layer's own tool deliberately does not import it, so a pass
    means the hook genuinely searched the official layer on the personal layer's
    behalf — not that ``sys.modules`` happened to be warm.  The helper imports
    its own layer's ``_layer_core``, which also pins that a derived helper's
    nested imports stay on *its* layer rather than following the caller's.
    """
    official = root / "official" / "tools"
    personal = root / "personal" / "tools"
    official.mkdir(parents=True)
    personal.mkdir(parents=True)
    (official / "_layer_core.py").write_text("WHO = 'OFFICIAL-CORE'\n", encoding="utf-8")
    (official / "_official_only.py").write_text(
        "import _layer_core as CORE\n\nMARKER = 'FROM-OFFICIAL:' + CORE.WHO\n", encoding="utf-8"
    )
    (official / "probe.py").write_text("SEEN = 'official-tool'\n", encoding="utf-8")
    (personal / "_layer_core.py").write_text("WHO = 'PERSONAL-CORE'\n", encoding="utf-8")
    (personal / "probe.py").write_text("from _official_only import MARKER as SEEN\n", encoding="utf-8")
    return make_layer("official", official), make_layer("personal", personal)


def _three_layer_fallback(root: Path) -> tuple[list[Layer], Path]:
    """Three layers where two of them ship ``_shared`` and the asker ships none.

    The witness for "priority, not open order".  Two layers cannot show it: "the
    layers other than the asker" would be a set of one and every ordering of it
    agrees.  Here the asking layer (personal) has no ``_shared``, so the fallback
    has to choose between enterprise (priority 10) and official (0) — and
    enterprise must win regardless of the order the layers were opened in.
    """
    official = root / "official" / "tools"
    enterprise = root / "enterprise" / "tools"
    personal = root / "personal" / "tools"
    for tools_dir in (official, enterprise, personal):
        tools_dir.mkdir(parents=True)
    (official / "_shared.py").write_text("WHO = 'official'\n", encoding="utf-8")
    (enterprise / "_shared.py").write_text("WHO = 'enterprise'\n", encoding="utf-8")
    (personal / "probe.py").write_text(
        "import _shared\n\nSEEN = _shared.WHO\nVIA_NAME = _shared.__name__\n", encoding="utf-8"
    )
    layers = [
        make_layer("official", official),
        make_layer("enterprise", enterprise),
        make_layer("personal", personal),
    ]
    return layers, enterprise


@pytest.fixture(autouse=True)
def _isolate_import_state() -> Iterator[None]:
    """Undo every global these criteria touch, so they cannot leak into each other.

    Teardown runs after the test body so the residue criterion can still observe
    what the load left behind before it is cleaned up.
    """
    path_before = list(sys.path)
    meta_before = list(sys.meta_path)
    modules_before = dict(sys.modules)
    depth_before = dict(tool_registry._path_scope_depth)
    stash_before = dict(tool_registry._private_module_stash)
    try:
        yield
    finally:
        sys.path[:] = path_before
        sys.meta_path[:] = meta_before
        for name in set(sys.modules) - set(modules_before):
            del sys.modules[name]
        sys.modules.update(modules_before)
        tool_registry._path_scope_depth.clear()
        tool_registry._path_scope_depth.update(depth_before)
        tool_registry._private_module_stash.clear()
        tool_registry._private_module_stash.update(stash_before)


# ── K1 — bare-name helper isolation ───────────────────────────────────────────


@BOTH_ORDERS
def test_k1_bare_name_helper_resolves_per_layer(tmp_path: Path, order: str) -> None:
    """K1: with both layers open, each tool binds its own bare-name helper.

    The case that fails without the hook: one ``sys.modules['_layer_helper']``
    slot, so whichever dir sits earlier on ``sys.path`` answers both lookups.
    Under the hook the two helpers occupy two scoped names, which cannot
    overwrite each other.
    """
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    assert modules["official/probe"].SEEN == "LAYER-OFFICIAL", (
        "official layer bound the personal layer's private helper"
    )
    assert modules["personal/probe"].SEEN == "LAYER-PERSONAL", (
        "personal layer bound the official layer's private helper"
    )


# ── K2 — dotted (package) helper isolation ────────────────────────────────────


@BOTH_ORDERS
@pytest.mark.parametrize("importer", sorted(DOTTED_IMPORTERS))
def test_k2_dotted_helper_resolves_per_layer(tmp_path: Path, order: str, importer: str) -> None:
    """K2: a private *package* resolves per layer, in all three import forms.

    Parametrised over the statement forms because they take different paths
    through CPython's import machinery and all three need the parent's plain name
    bound (scaffolding 1).  Covering only the first form would declare the kernel
    correct while the others were broken.

    What this does *not* cover is the scope package: all three importers live in
    one layer and exec in glob order, so ``probe.py`` runs first and binds ``sub``
    as an attribute on the scoped parent, which the later forms then read.  That
    is K6's job.
    """
    official = _dotted_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _dotted_layer(tmp_path, "personal", "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    assert modules[f"official/{importer}"].SEEN == "LAYER-OFFICIAL-sub", (
        "official layer bound the personal layer's private package"
    )
    assert modules[f"personal/{importer}"].SEEN == "LAYER-PERSONAL-sub", (
        "personal layer bound the official layer's private package"
    )


# ── K3 — cross-layer derivation ───────────────────────────────────────────────


@BOTH_ORDERS
def test_k3_personal_tool_derives_from_official_helper(tmp_path: Path, order: str) -> None:
    """K3: a personal tool importing an official-only helper still resolves.

    Isolation must not become a wall: a name absent from the asking layer is
    looked for in the others.  The marker also carries the official layer's own
    ``_layer_core``, so a derived helper whose nested import followed the
    *caller's* layer would read ``PERSONAL-CORE`` and fail here.
    """
    official, personal = _derivation_layers(tmp_path)

    modules = _exec_layers_together(_ordered(official, personal, order))

    assert modules["personal/probe"].SEEN == "FROM-OFFICIAL:OFFICIAL-CORE", (
        "the personal layer could not reach the official layer's private helper, "
        "or the derived helper's own import followed the caller's layer"
    )


# ── K4 — no residue ───────────────────────────────────────────────────────────


def test_k4_no_private_module_residue(tmp_path: Path) -> None:
    """K4: after the load, no private module of either layer stays reachable.

    Covers all four globals the kernel touches.  ``sys.modules`` is inspected
    directly because the residue *is* the subject here, not an implementation
    detail standing in for one: a plain ``_layer_helper`` left behind is exactly
    what makes the next unrelated load bind the wrong file.
    """
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _dotted_layer(tmp_path, "personal", "LAYER-PERSONAL")

    path_before = list(sys.path)
    meta_before = list(sys.meta_path)

    _exec_layers_together([official, personal])

    assert sys.path == path_before, "sys.path was not restored"
    assert sys.meta_path == meta_before, "the layer import hook was left installed"
    assert _private_modules_under(tmp_path) == [], (
        f"private modules still reachable by plain name: {_private_modules_under(tmp_path)}"
    )
    assert [n for n in sys.modules if n.startswith(SCOPE_PREFIX)] == [], "scoped layer modules survived the load"
    # Stash-held residue counts as residue: the kernel restores it into
    # ``sys.modules`` under its plain name the next time that dir opens, so a
    # plain name parked there is a delayed version of the same leak.
    assert _stashed_under(tmp_path) == {}, f"plain names parked in the kernel stash: {_stashed_under(tmp_path)}"


def _stashed_under(root: Path) -> dict[str, list[str]]:
    """What the kernel's stash is holding for tools dirs under *root*."""
    return {
        entry: sorted(names)
        for entry, names in tool_registry._private_module_stash.items()
        if root in Path(entry).parents
    }


def _private_modules_under(root: Path) -> list[str]:
    """Plain (unscoped) ``_``-prefixed ``sys.modules`` entries with a file under *root*."""
    found: list[str] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("_"):
            continue
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        try:
            if root in Path(origin).resolve().parents:
                found.append(name)
        except OSError:
            continue
    return sorted(found)


# ── K5 — one module object per layer ──────────────────────────────────────────


@BOTH_ORDERS
def test_k5_same_layer_importers_share_one_helper_object(tmp_path: Path, order: str) -> None:
    """K5: two tools in one layer get the *same* helper module object.

    Renaming must not turn into re-executing.  ``_background_process_registry``
    keeps its live-process table at module level; a second module object would
    split that table so the layer's second tool could not see the first tool's
    background processes.  Judged by identity *and* by a mutation through one
    binding being visible via the other, so a shell that merely copied the module
    would not pass.
    """
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    first = modules["official/probe"].HELPER
    second = modules["official/probe_second"].HELPER
    assert first is second, "the layer's two importers got two different helper module objects"
    first.STATE.append("from-first")
    assert second.STATE == ["from-first"], "module-level state did not survive between the layer's two importers"
    # And the other layer's helper must be a *different* object with its own state.
    assert modules["personal/probe"].HELPER is not first, "both layers share one helper module object"
    assert modules["personal/probe"].HELPER.STATE == [], "the personal layer saw the official layer's module state"


# ── K6 — the parent package resolves without a warming importer ───────────────

# Each shape reaches the scope package by a different route, and every one of
# them ships in this repo today.  ``sole_form`` matters: with several importers in
# a layer the first one warms the parent attribute and the later ones never walk
# up to ``psi_layer_<token>``, so the criterion would stop being load-bearing —
# green while the scaffolding it names was absent.
_K6_SHAPES = {
    "from_pkg_import_sub": (
        lambda root, layer_id, marker: _dotted_layer(root, layer_id, marker, sole_form="probe_from_pkg"),
        "probe_from_pkg",
        "-sub",
    ),
    "relative_import": (_relative_import_layer, "probe", "-rel"),
}


@BOTH_ORDERS
@pytest.mark.parametrize("shape", sorted(_K6_SHAPES))
def test_k6_parent_package_resolves_without_a_warming_importer(tmp_path: Path, order: str, shape: str) -> None:
    """K6: scaffolding 2 — ``psi_layer_<token>`` must exist as a package.

    The trigger is whether the parent is reached *as a package* rather than as an
    already-bound attribute; nesting depth is not it.  Only one importer per layer
    here, so nothing warms the attribute first.
    """
    build, stem, expected = _K6_SHAPES[shape]
    root = tmp_path / shape
    official = build(root, "official", "LAYER-OFFICIAL")
    personal = build(root, "personal", "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    for layer_id in ("official", "personal"):
        seen = modules[f"{layer_id}/{stem}"].SEEN
        want = f"LAYER-{layer_id.upper()}{expected}"
        assert seen == want, f"{layer_id} layer could not resolve its private package via the {shape} form"


# ── K7 — coexistence with the kernel's stash/restore ──────────────────────────


def test_k7_layered_load_leaves_the_kernel_stash_empty(tmp_path: Path) -> None:
    """K7: the hook and the existing stash/restore do not fight.

    ``_stash_private_modules`` collects plain ``sys.modules`` entries whose file
    sits in the closing dir.  Under the hook the private modules are registered
    under scoped names and the plain alias is already gone by the time a scope
    closes, so there is nothing for it to find: it becomes dead weight rather
    than a conflicting second mechanism.  Which answers "must stash/restore be
    removed before layering ships" — no, but it stops doing anything.
    """
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _dotted_layer(tmp_path, "personal", "LAYER-PERSONAL")

    _exec_layers_together([official, personal])

    assert _stashed_under(tmp_path) == {}, f"the kernel stash captured scoped modules: {_stashed_under(tmp_path)}"


# ── K8 — fallback follows priority, not open order ───────────────────────────

# Three layers, and all six open orders: the fourth piece of scaffolding is
# invisible with two.  Parametrising over every permutation is what makes "open
# order does not decide" an assertion rather than a hope — a single order could
# agree with priority by luck.
_THREE_LAYER_ORDERS = [
    (0, 1, 2),
    (0, 2, 1),
    (1, 0, 2),
    (1, 2, 0),
    (2, 0, 1),
    (2, 1, 0),
]


@pytest.mark.parametrize("open_order", _THREE_LAYER_ORDERS, ids=lambda o: "-".join(map(str, o)))
def test_k8_cross_layer_fallback_follows_priority(tmp_path: Path, open_order: tuple[int, ...]) -> None:
    """K8: scaffolding 4 — the fallback picks the highest-priority layer that has it.

    The asking layer ships no ``_shared``, so resolution falls through to the two
    that do.  Enterprise (10) must win over official (0) in every open order.

    ``VIA_NAME`` is read as well as the marker: the marker alone could be right
    because ``sys.path`` order happened to agree, while the module was resolved by
    the normal machinery and not by the hook at all.  The scoped name is the only
    evidence that lands on the hook's own layer.
    """
    layers, enterprise_dir = _three_layer_fallback(tmp_path)
    opened = [layers[i] for i in open_order]

    modules = _exec_layers_together(opened)

    probe = modules["personal/probe"]
    assert probe.SEEN == "enterprise", (
        f"fallback picked the wrong layer's _shared (open order {open_order}); "
        "resolution is following open order rather than Layer.priority"
    )
    want_name = scoped_module_name("enterprise", "_shared")
    via_name = probe.VIA_NAME
    assert via_name == want_name, f"_shared was not resolved through the layer hook: {via_name!r}"
    assert (enterprise_dir / "_shared.py").is_file()


# ── K9 — the asking layer outranks priority ───────────────────────────────────


@pytest.mark.parametrize("open_order", _THREE_LAYER_ORDERS, ids=lambda o: "-".join(map(str, o)))
def test_k9_asking_layer_beats_a_higher_priority_layer(tmp_path: Path, open_order: tuple[int, ...]) -> None:
    """K9: a layer's own helper wins even when a higher-priority layer has that name.

    This is the semantic decision, asserted head-on rather than left to prose: a
    ``_``-prefixed helper is a layer's *implementation*, not its addressable
    surface, so the personal layer happening to ship ``_shared`` is no reason for
    the official layer's tool to bind it.  The addressable surface is the public
    tool name, where ``ToolRegistry`` applies last-wins instead.

    Every layer ships ``_shared`` *and* a tool importing it, so all three assert
    at once and in all six open orders.
    """
    dirs = {}
    for layer_id in ("official", "enterprise", "personal"):
        tools_dir = tmp_path / layer_id / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "_shared.py").write_text(f"WHO = {layer_id!r}\n", encoding="utf-8")
        (tools_dir / "probe.py").write_text(
            "import _shared\n\nSEEN = _shared.WHO\nVIA_NAME = _shared.__name__\n", encoding="utf-8"
        )
        dirs[layer_id] = tools_dir
    layers = [make_layer(layer_id, tools_dir) for layer_id, tools_dir in dirs.items()]

    modules = _exec_layers_together([layers[i] for i in open_order])

    for layer_id in dirs:
        probe = modules[f"{layer_id}/probe"]
        seen = probe.SEEN
        want_name = scoped_module_name(layer_id, "_shared")
        assert seen == layer_id, (
            f"{layer_id} layer bound another layer's _shared (open order {open_order}); "
            "priority is overriding the asking layer's own implementation"
        )
        via_name = probe.VIA_NAME
        assert via_name == want_name, f"{layer_id}'s _shared was not resolved through the layer hook: {via_name!r}"


# ── K10 — scope tokens are distinct per layer ─────────────────────────────────


def test_k10_scope_token_is_distinct_for_paths_with_the_same_name() -> None:
    """K10: two layers whose dirs share a leaf name still get two scope names.

    ``layer_id`` is a tools-dir path in production, and every layer's dir is
    called ``tools``.  If the scope segment were just the sanitised leaf, all
    layers would collapse onto one key and the isolation would be gone while
    every path-shaped id still looked plausible in a traceback.
    """
    first = scope_package_name("/srv/official/tools")
    second = scope_package_name("/srv/personal/tools")

    assert first != second, "two different layers produced the same scope package name"
    assert first.startswith(SCOPE_PREFIX) and second.startswith(SCOPE_PREFIX)
    # Usable as a dotted module name: no separators, no dots, no spaces.
    for name in (first, second):
        assert name.replace(".", "").isidentifier() and "." not in name.removeprefix(SCOPE_PREFIX), name
    assert scope_package_name("/srv/official/tools") == first, "the token is not stable across calls"


# ── K11 — ToolRegistry.load_layers ────────────────────────────────────────────


def _callable_layer(root: Path, layer_id: str, marker: str, tool_name: str) -> Layer:
    """A layer whose public tool is an async function returning its helper's marker."""
    tools_dir = root / layer_id / "tools"
    pkg = tools_dir / "_layer_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub.py").write_text("import _layer_core as CORE\n\nMARKER = CORE.WHO\n", encoding="utf-8")
    (tools_dir / "_layer_core.py").write_text(f"WHO = {marker!r}\n", encoding="utf-8")
    (tools_dir / "probe.py").write_text(
        textwrap.dedent(
            f"""
            from _layer_pkg.sub import MARKER


            async def {tool_name}() -> str:
                \"\"\"Report which layer's helper this tool bound.\"\"\"
                return MARKER
            """
        ),
        encoding="utf-8",
    )
    return make_layer(layer_id, tools_dir)


@pytest.mark.anyio
@BOTH_ORDERS
async def test_k11_load_layers_binds_each_layers_own_helper(tmp_path: Path, order: str) -> None:
    """K11: the criteria hold through ``ToolRegistry.load_layers``, not just the shell.

    The shell above calls ``layers_open`` directly, which is the right seam for
    the resolution criteria but leaves open whether the registry's own loader is
    wired to it.  Here the tools are *called* through ``get()``, so a mis-wiring
    would surface as one layer's tool returning the other layer's marker.
    """
    official = _callable_layer(tmp_path, "official", "LAYER-OFFICIAL", "which_official")
    personal = _callable_layer(tmp_path, "personal", "LAYER-PERSONAL", "which_personal")

    registry = await tool_registry.ToolRegistry.load_layers(_ordered(official, personal, order), "s1")

    assert sorted(registry.tools) == ["which_official", "which_personal"], (
        f"both layers' tools should be registered: {sorted(registry.tools)}"
    )
    official_tool = registry.get("which_official")
    personal_tool = registry.get("which_personal")
    assert official_tool is not None and personal_tool is not None
    assert await official_tool() == "LAYER-OFFICIAL", "the official layer's tool bound the personal layer's helper"
    assert await personal_tool() == "LAYER-PERSONAL", "the personal layer's tool bound the official layer's helper"
    assert _private_modules_under(tmp_path) == [], (
        f"load_layers left private modules reachable: {_private_modules_under(tmp_path)}"
    )
    assert [n for n in sys.modules if n.startswith(SCOPE_PREFIX)] == [], "load_layers left scoped modules behind"


@pytest.mark.anyio
async def test_k12_load_layers_resolves_same_named_tool_to_the_top_layer(tmp_path: Path) -> None:
    """K12: for a *public* tool name, the highest-priority layer wins.

    The other half of the semantics: private helpers stay per layer (K9), while
    the addressable surface is last-wins in priority order.  ``tools`` and
    ``get()`` have to agree on the winner, or the LLM would be shown one layer's
    schema while another layer's body ran.
    """
    official = _callable_layer(tmp_path, "official", "LAYER-OFFICIAL", "which_layer")
    personal = _callable_layer(tmp_path, "personal", "LAYER-PERSONAL", "which_layer")

    # Opened official-last, so a loader trusting open order would answer official.
    registry = await tool_registry.ToolRegistry.load_layers([personal, official], "s1")

    tool = registry.get("which_layer")
    assert tool is not None
    assert await tool() == "LAYER-PERSONAL", "the higher-priority layer should own the shared public tool name"
    assert registry.tools["which_layer"].description, "the winning tool should carry its own metadata"


# ── mutation red-check ────────────────────────────────────────────────────────

# Each mutation must break the criteria it is responsible for and leave the rest
# alone.  A mutation that reddens everything would mean the criteria shadow each
# other and cannot localise a regression; one that reddens nothing means the
# behaviour it removed is not load-bearing anywhere.  Recorded as executable
# assertions rather than a commit-message claim so the mapping cannot rot.


def _k1_holds(root: Path) -> bool:
    official = _bare_layer(root, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(root, "personal", "LAYER-PERSONAL")
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    return modules["official/probe"].SEEN == "LAYER-OFFICIAL" and modules["personal/probe"].SEEN == "LAYER-PERSONAL"


def _k2_holds(root: Path) -> bool:
    official = _dotted_layer(root, "official", "LAYER-OFFICIAL")
    personal = _dotted_layer(root, "personal", "LAYER-PERSONAL")
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    for layer_id in ("official", "personal"):
        want = f"LAYER-{layer_id.upper()}-sub"
        for importer in DOTTED_IMPORTERS:
            seen = modules[f"{layer_id}/{importer}"].SEEN
            if seen != want:
                return False
    return True


def _k3_holds(root: Path) -> bool:
    official, personal = _derivation_layers(root)
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    return modules["personal/probe"].SEEN == "FROM-OFFICIAL:OFFICIAL-CORE"


def _k4_holds(root: Path) -> bool:
    official = _bare_layer(root, "official", "LAYER-OFFICIAL")
    personal = _dotted_layer(root, "personal", "LAYER-PERSONAL")
    try:
        _exec_layers_together([official, personal])
    except Exception:
        return False
    return (
        _private_modules_under(root) == []
        and _stashed_under(root) == {}
        and [n for n in sys.modules if n.startswith(SCOPE_PREFIX)] == []
    )


def _k5_holds(root: Path) -> bool:
    official = _bare_layer(root, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(root, "personal", "LAYER-PERSONAL")
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    return modules["official/probe"].HELPER is modules["official/probe_second"].HELPER


def _k6_holds(root: Path) -> bool:
    for shape, (build, stem, expected) in _K6_SHAPES.items():
        shape_root = root / shape
        layers = [build(shape_root, "official", "LAYER-OFFICIAL"), build(shape_root, "personal", "LAYER-PERSONAL")]
        try:
            modules = _exec_layers_together(layers)
        except Exception:
            return False
        for layer_id in ("official", "personal"):
            seen = modules[f"{layer_id}/{stem}"].SEEN
            want = f"LAYER-{layer_id.upper()}{expected}"
            if seen != want:
                return False
    return True


def _k8_holds(root: Path) -> bool:
    for index, open_order in enumerate(_THREE_LAYER_ORDERS):
        layers, _ = _three_layer_fallback(root / f"order{index}")
        try:
            modules = _exec_layers_together([layers[i] for i in open_order])
        except Exception:
            return False
        probe = modules["personal/probe"]
        seen = probe.SEEN
        via_name = probe.VIA_NAME
        want_name = scoped_module_name("enterprise", "_shared")
        if seen != "enterprise" or via_name != want_name:
            return False
    return True


def _k9_holds(root: Path) -> bool:
    dirs = {}
    for layer_id in ("official", "enterprise", "personal"):
        tools_dir = root / layer_id / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "_shared.py").write_text(f"WHO = {layer_id!r}\n", encoding="utf-8")
        (tools_dir / "probe.py").write_text("import _shared\n\nSEEN = _shared.WHO\n", encoding="utf-8")
        dirs[layer_id] = tools_dir
    layers = [make_layer(layer_id, tools_dir) for layer_id, tools_dir in dirs.items()]
    try:
        modules = _exec_layers_together(layers)
    except Exception:
        return False
    for layer_id in dirs:
        seen = modules[f"{layer_id}/probe"].SEEN
        if seen != layer_id:
            return False
    return True


CRITERIA = {
    "K1": _k1_holds,
    "K2": _k2_holds,
    "K3": _k3_holds,
    "K4": _k4_holds,
    "K5": _k5_holds,
    "K6": _k6_holds,
    "K8": _k8_holds,
    "K9": _k9_holds,
}


@contextmanager
def _sandboxed() -> Iterator[None]:
    """Restore import globals around one criterion run.

    Load-bearing, and the sibling probe found it the hard way: with the criteria
    sharing one process, a plain-name module leaked by the *first* criterion
    satisfied the second one's import straight from ``sys.modules``, so the hook
    was never consulted and no residue was created under that criterion's own
    tmp dir — the residue criterion came out green while the leak it exists to
    catch was happening one directory over.
    """
    path_before = list(sys.path)
    meta_before = list(sys.meta_path)
    modules_before = dict(sys.modules)
    stash_before = dict(tool_registry._private_module_stash)
    try:
        yield
    finally:
        sys.path[:] = path_before
        sys.meta_path[:] = meta_before
        for name in set(sys.modules) - set(modules_before):
            del sys.modules[name]
        sys.modules.update(modules_before)
        tool_registry._private_module_stash.clear()
        tool_registry._private_module_stash.update(stash_before)


# mutation → the criteria it must redden.  Anything not listed must stay green.
#
# **Measured, not predicted** — three of these differ from what I expected before
# running them, and each difference had a cause worth keeping:
#
# ``drop_layer_prefix`` does *not* redden K5 (one module object per layer), though
# collapsing every layer onto one scope name plainly breaks isolation.  K5 asks
# whether a layer's two importers share one object, and with one shared key they
# still do — more sharing, not less.  The criterion that catches the collapse is
# K9, where the *wrong* layer's object comes back.
#
# ``ignore_asking_layer`` and ``priority_over_asking_layer`` collapse to the same
# kernel behaviour (rank purely by priority) and so to the same set.  Both are
# kept because they are different *questions*: the first asks whether tracking the
# asker matters at all, the second is the rival semantics someone will propose
# again.  That they redden K1/K2/K9 is the evidence that pure priority does not
# merely reorder fallback — it dismantles per-layer isolation.
#
# Both also redden K3 and K6, which I had not predicted:
#   * K3 — the official helper's own ``import _layer_core`` stops following the
#     official layer and reads ``FROM-OFFICIAL:PERSONAL-CORE`` (measured).  So K3
#     pins two things: that fallback reaches another layer at all, *and* that a
#     derived helper's nested imports stay on the layer that owns it.
#   * K6 — its two layers both ship the package, so with the asker ignored the
#     higher-priority layer answers for both and the markers cross.
#
# ``ignore_layer_priority`` and ``reverse_layer_priority`` redden only K8: with the
# asking layer still answering for itself, mis-ranking *the others* is only
# visible where the asker ships nothing — which is why this needs three layers.
#
# ``leak_plain_aliases`` reddens the isolation criteria as well as the residue
# one, and that is kept rather than split: a plain name left bound *is* the
# isolation failure, because the next file's ``import _helper`` hits
# ``sys.modules`` and the hook is never consulted.
#
# ``no_module_reuse`` does not redden K6: with the scope package registered,
# re-exec'ing a helper still resolves the parent correctly.
EXPECTED_BREAKAGE = {
    "drop_layer_prefix": {"K1", "K2", "K6", "K9"},
    "ignore_asking_layer": {"K1", "K2", "K3", "K6", "K9"},
    "no_cross_layer_fallback": {"K3", "K8"},
    "no_module_reuse": {"K5"},
    "leak_plain_aliases": {"K1", "K2", "K4", "K6", "K9"},
    "no_scope_package": {"K6"},
    "ignore_layer_priority": {"K8"},
    "reverse_layer_priority": {"K8"},
    "priority_over_asking_layer": {"K1", "K2", "K3", "K6", "K9"},
}


@pytest.mark.parametrize("knob", sorted(EXPECTED_BREAKAGE))
def test_mutations_redden_exactly_their_own_criteria(tmp_path: Path, knob: str) -> None:
    """Disabling one kernel behaviour must break its criteria and only those."""
    expected = EXPECTED_BREAKAGE[knob]
    broken = set()
    with mutated(knob):
        for name, check in CRITERIA.items():
            with _sandboxed():
                if not check(tmp_path / name):
                    broken.add(name)
    assert broken == expected, f"mutation {knob!r} broke {sorted(broken)}, expected exactly {sorted(expected)}"


def test_every_criterion_is_covered_by_some_mutation() -> None:
    """Every criterion in ``CRITERIA`` must be reddened by at least one mutation.

    A criterion no mutation can break is either testing nothing or testing
    something the kernel does not decide.  This is the check that would have
    caught the scope package having no witness at all.
    """
    covered = set().union(*EXPECTED_BREAKAGE.values())
    assert covered == set(CRITERIA), (
        f"criteria with no mutation to redden them: {sorted(set(CRITERIA) - covered)}; "
        f"mutations naming an unknown criterion: {sorted(covered - set(CRITERIA))}"
    )
