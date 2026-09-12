"""Feasibility probe for design A1: per-layer import hooks that *rename* modules.

``test_layer_isolation_probe`` pins down today's hazard: private helpers enter
``sys.modules`` under their bare name (``_helper``), so two content layers
shipping a same-named helper share one slot and the second layer silently binds
the first layer's file.  ``tool_registry``'s stash/restore pair only papers over
that while one tools dir is in scope at a time, which layering breaks.

A1's claim is that the collision can be made *impossible* rather than avoided:
each layer gets a ``sys.meta_path`` hook that loads ``_helper`` under the
scoped name ``psi_layer_<layer>._helper``.  Two layers, two keys, no shared
slot.  Tool files keep writing ``import _helper``; the hook decides which
layer's file that resolves to based on which layer is *asking*.

This file contains a prototype hook (``_LayerImportHook``) written only to
answer "is A1 buildable", plus the criteria that judge it.  ``ToolRegistry`` is
not touched, and nothing here is proposed as the shipping implementation.

Three things A1 needs that are not obvious from the design sketch, each found
by measurement rather than reading CPython:

1. A dotted helper (``_pkg.sub``) cannot be renamed alone.  CPython imports the
   parent first and then looks it up **by its plain name** in ``sys.modules``,
   so renaming ``_pkg`` to ``psi_layer_x._pkg`` makes ``import _pkg.sub`` raise
   ``KeyError: '_pkg'``.  The hook therefore also binds the plain name, but
   only for the duration of the importing tool file, and drops it after.
2. Any import that resolves the parent *as a package* rather than as an
   already-bound attribute additionally needs ``psi_layer_<layer>`` itself to
   exist in ``sys.modules``, or it raises ``ModuleNotFoundError: No module named
   'psi_layer_official'``.  Two shapes reach it, both shipping today:
   ``from _pkg import sub`` and an in-package relative import (``from . import
   sibling``).  Depth does not: ``_pkg/sub/deeper.py`` needs nothing extra.
   Pinned by P6, which loads *one* importer per layer — with several, the first
   warms the attribute and the criterion stops being load-bearing.
3. The scoped-name cache hit must re-bind the plain name too, otherwise the
   second tool file to import a dotted helper fails where the first succeeded.
4. The search order has to follow an explicit layer *rank*, not the order the
   layers were opened in.  ``Layer.priority`` carries it (higher = closer to the
   user) and ``_ranked_layers`` sorts on it.  Found only once a third layer
   existed: with two layers "the layers other than the asker" is a set of one and
   every ordering of it agrees.  Pinned by R5 in the sibling file, which is also
   the sole witness for both priority knobs — nothing in *this* file can see them.
   Rank is a field rather than list position or a guess from ``layer_id`` because
   the real ids are tools-dir paths, and either shortcut would put the same bug
   back in a new place.

As in the sibling probe, every verdict is read back out of a ``MARKER`` value
that reached the tool module.  ``sys.modules`` internals are inspected only by
the residue criterion (P4), where the residue *is* the subject.
"""

from __future__ import annotations

import contextvars
import importlib.abc
import importlib.util
import sys
import textwrap
import types
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

from psi_agent.session import tool_registry
from psi_agent.session.tool_registry import _tools_dir_on_sys_path

# ── A1 prototype: a renaming import hook per layer ────────────────────────────

_SCOPE_PREFIX = "psi_layer_"

# Which layer's code is currently executing.  A ``ContextVar`` rather than an
# attribute because it must follow *nested* imports: when a personal tool pulls
# in an official helper, that helper's own ``import _core`` has to resolve
# against the official layer, not the personal one that started the chain.
_asking_layer: contextvars.ContextVar[str] = contextvars.ContextVar("psi_asking_layer")

# The mutation knobs used for the red-check.  Each disables exactly one
# behaviour of the hook; the mapping from knob to failing criterion is recorded
# in the commit message.
MUTATIONS = {
    "drop_layer_prefix": False,  # scope names without the layer id → one shared key
    "ignore_asking_layer": False,  # never prefer the asking layer's own file
    "no_cross_layer_fallback": False,  # only ever look in the asking layer
    "no_module_reuse": False,  # re-exec a helper on every import
    "leak_plain_aliases": False,  # never drop the transient plain-name binding
    "no_scope_package": False,  # never register ``psi_layer_<layer>`` as a package
    "ignore_layer_priority": False,  # search in open order instead of priority order
    "reverse_layer_priority": False,  # search least-specific layer first
    # Not a scaffolding removal but the *other* answer to "asking layer first or
    # highest priority first" — rank every layer purely by priority, so a
    # higher-priority layer answers even when the asking layer ships the name
    # itself.  A knob rather than prose so the question is settled by measuring
    # which criteria it breaks; see R7.
    "priority_over_asking_layer": False,
}


@dataclass(frozen=True)
class Layer:
    """One content layer: an id used in scoped module names, its tools dir, its rank.

    ``priority`` is what the search order follows: **higher wins**, so the layer
    closest to the user outranks the ones behind it.  It is a field rather than
    the caller's list order because list order is *open* order — glob order in
    production, which no layer controls — and rather than something inferred from
    ``layer_id`` because the real ids are tools-dir paths (see ``tool_registry``'s
    ``_layer_id``), not the words "personal"/"official".  Both of those were the
    bug this field exists to remove; keeping either as a fallback would move it
    rather than fix it.

    Whoever assembles the layers knows their ranks and states them here.  The
    hook never looks at ``layer_id`` to decide precedence.
    """

    layer_id: str
    tools_dir: Path
    priority: int


# The shipping ladder, as data the fixtures look their rank up in.  This lives at
# the test-data level on purpose: it is the *assembler's* knowledge of which layer
# is which, the same knowledge production has when it builds the layer list.  The
# resolution logic below never consults it.
#
# Gaps of 10 so a tier can be inserted between two of these without renumbering.
LAYER_PRIORITY = {"official": 0, "enterprise": 10, "personal": 20}


def make_layer(layer_id: str, tools_dir: Path) -> Layer:
    """A ``Layer`` whose priority comes from ``LAYER_PRIORITY``.

    Every fixture in this file and its sibling goes through here, so no test can
    accidentally state a rank inline and drift from the ladder the criteria judge
    against.  An id outside the ladder is a mistake worth failing on rather than
    defaulting: a silently-zero priority would put the layer behind official and
    the symptom would look like a resolution bug.
    """
    if layer_id not in LAYER_PRIORITY:
        raise KeyError(f"no priority declared for layer {layer_id!r}; known: {sorted(LAYER_PRIORITY)}")
    return Layer(layer_id, tools_dir, LAYER_PRIORITY[layer_id])


def _scoped_name(layer_id: str, plain: str) -> str:
    """The name a layer's private module is registered under.

    The layer id is the whole point: it is what makes two layers' ``_helper``
    two different ``sys.modules`` keys instead of one contested slot.
    """
    if MUTATIONS["drop_layer_prefix"]:
        return f"{_SCOPE_PREFIX}shared.{plain}"
    return f"{_SCOPE_PREFIX}{layer_id}.{plain}"


def _find_source(tools_dir: Path, fullname: str) -> tuple[Path, list[str] | None] | None:
    """Locate *fullname* under *tools_dir*, as ``(file, submodule search dirs)``.

    Handles both shapes the repo ships: a bare module (``_runtime_paths.py``)
    and a package (``_feishu/`` with 15 submodules, imported as ``_feishu.auth``).
    """
    *parents, last = fullname.split(".")
    base = tools_dir
    for part in parents:
        base = base / part
    package_init = base / last / "__init__.py"
    if package_init.is_file():
        return package_init, [str(base / last)]
    module_file = base / f"{last}.py"
    if module_file.is_file():
        return module_file, None
    return None


class _ScopedLoader(importlib.abc.Loader):
    """Executes a layer's private module, with the layer pinned as "who is asking".

    Also binds the module's *plain* name for as long as the import is in
    flight.  CPython resolves a dotted child by looking its parent up under the
    plain name, so without this ``import _pkg.sub`` raises ``KeyError:
    '_pkg'``.  The binding is transient: ``_LayerImportHook.release_aliases``
    removes it once the tool file that triggered the import has finished, so
    two layers are never simultaneously reachable under the same plain name.
    """

    def __init__(self, inner: importlib.abc.Loader, plain: str, layer_id: str, hook: _LayerImportHook) -> None:
        self._inner = inner
        self._plain = plain
        self._layer_id = layer_id
        self._hook = hook

    def create_module(self, spec: ModuleSpec) -> types.ModuleType | None:
        return self._inner.create_module(spec)

    def exec_module(self, module: types.ModuleType) -> None:
        self._hook.bind_alias(self._plain, module)
        token = _asking_layer.set(self._layer_id)
        try:
            self._inner.exec_module(module)
        finally:
            _asking_layer.reset(token)


class _ReuseLoader(importlib.abc.Loader):
    """Hands back an already-exec'd module instead of running it a second time.

    Without this, a layer's second importer would get a fresh module object and
    module-level state (``_background_process_registry``'s live-process table)
    would split in two — the hazard P5 guards.
    """

    def __init__(self, module: types.ModuleType, plain: str, hook: _LayerImportHook) -> None:
        self._module = module
        self._plain = plain
        self._hook = hook

    def create_module(self, spec: ModuleSpec) -> types.ModuleType | None:
        return self._module

    def exec_module(self, module: types.ModuleType) -> None:
        # Re-bind the plain name even on the cache-hit path: a dotted child's
        # parent lookup needs it whether or not the parent was already loaded.
        self._hook.bind_alias(self._plain, module)


class _LayerImportHook(importlib.abc.MetaPathFinder):
    """Resolves bare-name private imports to the asking layer's own file.

    Installed once on ``sys.meta_path`` for a set of layers.  Only claims names
    whose first segment starts with ``_``; everything else (``anyio``,
    ``psi_agent.*``) falls through to the normal machinery untouched.
    """

    def __init__(self, layers: Sequence[Layer]) -> None:
        self._layers = list(layers)
        self._aliases: set[str] = set()

    def _ranked_layers(self) -> list[Layer]:
        """Every layer, most-specific first, by ``Layer.priority``.

        Sorting rather than trusting ``self._layers`` is the fourth piece of
        scaffolding.  The constructor is handed the layers in *open* order — glob
        order in production — so relying on it made resolution depend on which
        layer happened to be opened first: R5 passed for ``[personal, enterprise,
        official]`` and failed for ``[official, enterprise, personal]``.  Two
        layers cannot show this, because "the other layer" is a set of one.

        ``sorted`` is stable, so layers sharing a priority keep their open order
        relative to each other.  That is the one remaining case where open order
        still decides, and it is a tie the assembler declared.
        """
        if MUTATIONS["ignore_layer_priority"]:
            return list(self._layers)
        return sorted(self._layers, key=lambda layer: layer.priority, reverse=not MUTATIONS["reverse_layer_priority"])

    def _search_order(self) -> list[Layer]:
        """The asking layer first, then the remaining layers by descending priority.

        Two rules, and only the second one is about precedence:

        * **The asking layer's own file always wins**, even when a
          higher-priority layer ships the same name.  This is not a preference
          among equals — it *is* the isolation A1 exists to provide, and P1, P2
          and R3 are already assertions of it: each has both layers shipping the
          same helper name and demands that each layer bind its own.  Measured,
          via the ``priority_over_asking_layer`` knob: ranking ahead of the asker
          reddens P1, P2, P3, P6, R3 and R7.  Private (``_``-prefixed) helpers are
          a layer's implementation, not its addressable surface, so a downstream
          layer that happens to name a helper the same must not silently replace
          an upstream layer's — that collision is the whole hazard.  Pinned
          head-on by R7.
        * **Priority orders the fallback**: names the asking layer does not ship
          are looked for most-specific-layer-first, so personal beats enterprise
          beats official.  Pinned by R5.
        """
        ranked = self._ranked_layers()
        asking = _asking_layer.get(None)
        if asking is None or MUTATIONS["ignore_asking_layer"] or MUTATIONS["priority_over_asking_layer"]:
            return ranked
        own = [layer for layer in ranked if layer.layer_id == asking]
        if MUTATIONS["no_cross_layer_fallback"]:
            return own
        return own + [layer for layer in ranked if layer.layer_id != asking]

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> ModuleSpec | None:
        if fullname.startswith(_SCOPE_PREFIX) or not fullname.partition(".")[0].startswith("_"):
            return None
        for layer in self._search_order():
            found = _find_source(layer.tools_dir, fullname)
            if found is None:
                continue
            source, search_locations = found
            self._ensure_scope_package(layer.layer_id)
            scoped = _scoped_name(layer.layer_id, fullname)
            cached = sys.modules.get(scoped)
            if cached is not None and not MUTATIONS["no_module_reuse"]:
                return importlib.util.spec_from_loader(scoped, _ReuseLoader(cached, fullname, self))
            spec = importlib.util.spec_from_file_location(scoped, source, submodule_search_locations=search_locations)
            if spec is None or spec.loader is None:
                return None
            spec.loader = _ScopedLoader(spec.loader, fullname, layer.layer_id, self)
            return spec
        return None

    def _ensure_scope_package(self, layer_id: str) -> None:
        """Register ``psi_layer_<layer>`` as a package.

        Needed whenever CPython has to resolve the *parent package's own module
        object* through the normal machinery, which walks up to
        ``psi_layer_<layer>`` and raises ``ModuleNotFoundError`` if that name is
        absent.  Two situations reach it, both shipping in this repo today:

        * ``from _pkg import sub`` — 6 sites under ``agents/feishu/tools/``.
        * an in-package relative import (``from . import sibling``, ``from .sub
          import X``), whether written in ``_pkg/__init__.py`` or in a
          submodule — 13 sites in ``agents/desktop/tools/_fusion_memory/``.

        Nesting depth is *not* what triggers it: ``_pkg/sub/deeper.py`` resolves
        fine without this, because each level is bound by ``bind_alias``.  What
        matters is whether the parent is reached as a package rather than as an
        already-bound attribute.  See ``_dotted_layer``'s ``sole_form`` for why
        the multi-importer fixture cannot witness this.
        """
        if MUTATIONS["no_scope_package"]:
            return
        name = _scoped_name(layer_id, "").rstrip(".")
        if name in sys.modules:
            return
        package = types.ModuleType(name)
        package.__path__ = []  # namespace-style: children are placed by us, not found on disk
        sys.modules[name] = package

    def bind_alias(self, plain: str, module: types.ModuleType) -> None:
        sys.modules[plain] = module
        self._aliases.add(plain)

    def release_aliases(self) -> None:
        """Drop every transient plain-name binding taken during one tool file."""
        if MUTATIONS["leak_plain_aliases"]:
            return
        for name in self._aliases:
            sys.modules.pop(name, None)
        self._aliases.clear()


# ── the loading shell (mirrors test_layer_isolation_probe) ────────────────────


@contextmanager
def _a1_layers_open(layers: Sequence[Layer]) -> Iterator[_LayerImportHook]:
    """Open every layer at once under A1, and tear the hook back down.

    Deliberately still enters ``_tools_dir_on_sys_path`` for each layer: A1 has
    to work in the world the kernel actually runs, where those dirs are on
    ``sys.path`` and the stash/restore pair is live.  Whether that coexistence
    is safe is itself measured, in ``test_a1_leaves_the_kernel_stash_empty``.
    """
    hook = _LayerImportHook(layers)
    with ExitStack() as stack:
        for layer in layers:
            stack.enter_context(_tools_dir_on_sys_path(layer.tools_dir))
        sys.meta_path.insert(0, hook)
        try:
            yield hook
        finally:
            sys.meta_path.remove(hook)
            hook.release_aliases()
            # Both the scoped modules and the synthetic ``psi_layer_<layer>``
            # parents share the prefix, so one sweep clears both.
            for name in [n for n in sys.modules if n.startswith(_SCOPE_PREFIX)]:
                del sys.modules[name]


def _exec_layers_together(layers: Sequence[Layer]) -> dict[str, types.ModuleType]:
    """Exec every layer's public tool files with all layers simultaneously open.

    Returns ``{"<layer>/<file stem>": module}``.  The plain-name aliases a file
    needed are released after *each* file, which is what makes the next file's
    imports resolve against its own layer rather than inheriting the previous
    one's bindings.
    """
    modules: dict[str, types.ModuleType] = {}
    with _a1_layers_open(layers) as hook:
        for layer in layers:
            for py_file in sorted(layer.tools_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                modules[f"{layer.layer_id}/{py_file.stem}"] = _exec_tool_file(py_file, layer, hook)
    return modules


def _exec_tool_file(py_file: Path, layer: Layer, hook: _LayerImportHook) -> types.ModuleType:
    """Compile + exec one public tool file with *layer* pinned as the asker."""
    module_name = f"psi_probe_a1_{layer.layer_id}_{py_file.stem}"
    module = types.ModuleType(module_name)
    module.__file__ = str(py_file)
    sys.modules[module_name] = module
    token = _asking_layer.set(layer.layer_id)
    try:
        exec(compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec"), module.__dict__)
    finally:
        _asking_layer.reset(token)
        hook.release_aliases()
    return module


def _ordered(official: Layer, personal: Layer, order: str) -> list[Layer]:
    """The two layers, in the requested load order."""
    return [official, personal] if order == "official-first" else [personal, official]


BOTH_ORDERS = pytest.mark.parametrize("order", ["official-first", "personal-first"])


# ── layer fixtures on disk ────────────────────────────────────────────────────


def _bare_layer(root: Path, layer_id: str, marker: str) -> Layer:
    """A layer with a bare-name private helper and two tools importing it."""
    tools_dir = root / layer_id / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "_layer_helper.py").write_text(
        textwrap.dedent(
            f"""
            MARKER = {marker!r}
            STATE = []
            """
        ),
        encoding="utf-8",
    )
    (tools_dir / "probe.py").write_text(
        textwrap.dedent(
            """
            import _layer_helper as HELPER
            from _layer_helper import MARKER as SEEN
            """
        ),
        encoding="utf-8",
    )
    # Second importer in the same layer: P5 compares the two files' bindings.
    (tools_dir / "probe_second.py").write_text(
        textwrap.dedent(
            """
            import _layer_helper as HELPER
            SEEN = HELPER.MARKER
            """
        ),
        encoding="utf-8",
    )
    return make_layer(layer_id, tools_dir)


def _derivation_layers(root: Path) -> tuple[Layer, Layer]:
    """Official ships a helper the personal layer imports but does not have.

    The official layer's own tool deliberately does not import it, so a pass
    means the hook genuinely searched the official layer on the personal
    layer's behalf — not that ``sys.modules`` happened to be warm.  The helper
    itself imports its own layer's ``_layer_core``, so this also pins that a
    derived helper's nested imports stay on *its* layer rather than following
    the caller's.
    """
    official = root / "official" / "tools"
    personal = root / "personal" / "tools"
    official.mkdir(parents=True)
    personal.mkdir(parents=True)
    (official / "_layer_core.py").write_text("WHO = 'OFFICIAL-CORE'\n", encoding="utf-8")
    (official / "_official_only.py").write_text(
        textwrap.dedent(
            """
            import _layer_core as CORE

            MARKER = "FROM-OFFICIAL:" + CORE.WHO
            """
        ),
        encoding="utf-8",
    )
    (official / "probe.py").write_text("SEEN = 'official-tool'\n", encoding="utf-8")
    (personal / "_layer_core.py").write_text("WHO = 'PERSONAL-CORE'\n", encoding="utf-8")
    (personal / "probe.py").write_text(
        textwrap.dedent(
            """
            from _official_only import MARKER as SEEN
            """
        ),
        encoding="utf-8",
    )
    return make_layer("official", official), make_layer("personal", personal)


DOTTED_IMPORTERS = {
    "probe": "from _layer_pkg.sub import MARKER as SEEN\n",
    "probe_from_pkg": "from _layer_pkg import sub\n\nSEEN = sub.MARKER\n",
    "probe_import_dotted": "import _layer_pkg.sub\n\nSEEN = _layer_pkg.sub.MARKER\n",
}


def _dotted_layer(root: Path, layer_id: str, marker: str, sole_form: str | None = None) -> Layer:
    """A layer whose helper is a package, in the shape ``agents/feishu/tools/_feishu/`` ships.

    The submodule imports its layer's bare-name sibling too (``_feishu/*.py``
    all do ``import _feishu_impl``), so this covers a nested private import
    resolving from inside an already-scoped module.

    *sole_form* writes only that one importer.  Load order is otherwise
    load-bearing in a way that hides a real failure: the files exec in glob
    order, so ``probe.py``'s ``from _layer_pkg.sub import X`` runs first and
    leaves ``sub`` set as an attribute on the scoped parent.  ``from _layer_pkg
    import sub`` then finds that attribute and never walks up to
    ``psi_layer_<layer>`` — so the multi-importer fixture cannot witness the
    scope package's absence, even though it names that form as its subject.  The
    repo has no such warming file: ``positive_negative_candidate_analyze.py``
    sorts first and its first touch of the package *is* ``from _pkg import``.
    """
    tools_dir = root / layer_id / "tools"
    package = tools_dir / "_layer_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sub.py").write_text(
        textwrap.dedent(
            """
            import _layer_core as CORE

            MARKER = CORE.WHO + "-sub"
            """
        ),
        encoding="utf-8",
    )
    (tools_dir / "_layer_core.py").write_text(f"WHO = {marker!r}\n", encoding="utf-8")
    wanted = DOTTED_IMPORTERS if sole_form is None else {sole_form: DOTTED_IMPORTERS[sole_form]}
    for name, source in wanted.items():
        (tools_dir / f"{name}.py").write_text(source, encoding="utf-8")
    return make_layer(layer_id, tools_dir)


def _relative_import_layer(root: Path, layer_id: str, marker: str) -> Layer:
    """A layer whose private package uses an in-package relative import.

    The shape ``agents/desktop/tools/_fusion_memory/`` ships: ``__init__.py``
    pulls a submodule in with ``from .journal import ...``, and submodules
    import each other the same way.  A relative import resolves the current
    package through the normal machinery, so it needs the scope package to exist
    even though no tool file writes ``from _pkg import sub``.
    """
    tools_dir = root / layer_id / "tools"
    package = tools_dir / "_layer_rel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from .sibling import VALUE as VALUE\n", encoding="utf-8")
    (package / "sibling.py").write_text("import _layer_core as CORE\n\nVALUE = CORE.WHO + '-rel'\n", encoding="utf-8")
    (tools_dir / "_layer_core.py").write_text(f"WHO = {marker!r}\n", encoding="utf-8")
    (tools_dir / "probe.py").write_text("import _layer_rel\n\nSEEN = _layer_rel.VALUE\n", encoding="utf-8")
    return make_layer(layer_id, tools_dir)


@pytest.fixture(autouse=True)
def _isolate_import_state() -> Iterator[None]:
    """Undo every global this probe touches, including ``sys.meta_path``.

    Teardown runs after the test body so the residue criterion can still
    observe what the load left behind before it is cleaned up.  Mutation knobs
    are reset here too, so a mutation run cannot bleed into later tests.
    """
    path_before = list(sys.path)
    meta_before = list(sys.meta_path)
    modules_before = dict(sys.modules)
    depth_before = dict(tool_registry._path_scope_depth)
    stash_before = dict(tool_registry._private_module_stash)
    mutations_before = dict(MUTATIONS)
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
        MUTATIONS.clear()
        MUTATIONS.update(mutations_before)


@contextmanager
def mutated(knob: str) -> Iterator[None]:
    """Turn one hook behaviour off, for the red-check in ``test_mutations_*``."""
    MUTATIONS[knob] = True
    try:
        yield
    finally:
        MUTATIONS[knob] = False


# ── A1-P1 — bare-name helper isolation ────────────────────────────────────────


@BOTH_ORDERS
def test_a1_p1_bare_name_helper_resolves_per_layer(tmp_path: Path, order: str) -> None:
    """A1-P1: with both layers open, each tool binds its own bare-name helper.

    This is the case that fails today (the sibling probe's xfail P1): one
    ``sys.modules['_layer_helper']`` slot, so whichever dir sits earlier on
    ``sys.path`` wins both lookups.  Under A1 the two helpers occupy
    ``psi_layer_official._layer_helper`` and
    ``psi_layer_personal._layer_helper``, which cannot overwrite each other.
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


# ── A1-P2 — dotted (package) helper isolation ─────────────────────────────────


@BOTH_ORDERS
@pytest.mark.parametrize("importer", ["probe", "probe_from_pkg", "probe_import_dotted"])
def test_a1_p2_dotted_helper_resolves_per_layer(tmp_path: Path, order: str, importer: str) -> None:
    """A1-P2: a private *package* also resolves per layer, in all three import forms.

    Parametrised over the statement forms because they take different paths
    through CPython's import machinery, and all three need the parent's plain
    name bound.  Testing only the first form would have declared A1 viable while
    the others were broken.

    What this criterion does *not* cover: the scope package
    ``psi_layer_<layer>``.  All three importers live in one layer and exec in
    glob order, so ``probe.py`` runs first and binds ``sub`` as an attribute on
    the scoped parent; ``probe_from_pkg.py``'s ``from _layer_pkg import sub``
    then reads that attribute instead of resolving the parent as a package.
    That is P6's job.
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


# ── A1-P3 — cross-layer derivation ────────────────────────────────────────────


@BOTH_ORDERS
def test_a1_p3_personal_tool_derives_from_official_helper(tmp_path: Path, order: str) -> None:
    """A1-P3: a personal tool importing an official-only helper still resolves.

    Isolation must not become a wall: the hook falls back through the layer
    order, so a name absent from the asking layer is looked for in the others.
    """
    official, personal = _derivation_layers(tmp_path)

    modules = _exec_layers_together(_ordered(official, personal, order))

    assert modules["personal/probe"].SEEN == "FROM-OFFICIAL:OFFICIAL-CORE", (
        "the personal layer could not reach the official layer's private helper, "
        "or the derived helper's own import followed the caller's layer"
    )


# ── A1-P4 — no residue ────────────────────────────────────────────────────────


def test_a1_p4_no_private_module_residue(tmp_path: Path) -> None:
    """A1-P4: after the load, no private module of either layer is left reachable.

    Covers all three globals A1 touches.  ``sys.modules`` is inspected directly
    here because the residue is the subject of the criterion, not an
    implementation detail standing in for one: a plain ``_layer_helper`` left
    behind is exactly what makes the *next* unrelated load bind the wrong file.
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
    assert [n for n in sys.modules if n.startswith(_SCOPE_PREFIX)] == [], "scoped layer modules survived the load"
    # Stash-held residue counts as residue: the kernel restores it into
    # ``sys.modules`` under its plain name the next time that dir opens, so a
    # plain name parked there is a delayed version of the same leak.  Checking
    # only ``sys.modules`` reported this criterion green while the leak was
    # happening — see ``test_a1_leaves_the_kernel_stash_empty``.
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


# ── A1-P5 — one module object per layer ───────────────────────────────────────


@BOTH_ORDERS
def test_a1_p5_same_layer_importers_share_one_helper_object(tmp_path: Path, order: str) -> None:
    """A1-P5: two tools in one layer get the *same* helper module object.

    Renaming must not turn into re-executing.  ``_background_process_registry``
    keeps its live-process table at module level; a second module object would
    split that table in two and the second tool would not see the first tool's
    background processes.  Judged by identity plus a mutation through one
    binding being visible via the other, so a shell that merely copies the
    module would not pass.
    """
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    for layer_id in ("official", "personal"):
        first = modules[f"{layer_id}/probe"].HELPER
        second = modules[f"{layer_id}/probe_second"].HELPER
        assert first is second, f"{layer_id}: the layer's two tools got different helper module objects"
        first.STATE.append("written-by-probe")
        assert second.STATE == ["written-by-probe"], (
            f"{layer_id}: module-level state is not shared between the layer's tools"
        )

    assert modules["official/probe"].HELPER is not modules["personal/probe"].HELPER, (
        "the two layers ended up sharing one helper module object"
    )


# ── A1-P6 — the scope package, witnessed without a warming importer ───────────

# shape → (layer builder, the public tool file's stem, expected marker suffix).
# Shared with ``_p6_holds`` so the criterion and its mutation-matrix twin cannot
# drift apart.
_P6_SHAPES = {
    "from_pkg_import_sub": (
        lambda root, layer_id, marker: _dotted_layer(root, layer_id, marker, sole_form="probe_from_pkg"),
        "probe_from_pkg",
        "-sub",
    ),
    "in_package_relative_import": (_relative_import_layer, "probe", "-rel"),
}


@BOTH_ORDERS
@pytest.mark.parametrize(
    "shape",
    ["from_pkg_import_sub", "in_package_relative_import"],
    ids=["from _pkg import sub", "from . import sibling"],
)
def test_a1_p6_parent_package_resolves_without_a_warming_importer(tmp_path: Path, order: str, shape: str) -> None:
    """A1-P6: the two shapes that resolve the parent *as a package* still work.

    Each layer here ships exactly one importer, so nothing has pre-bound the
    submodule as an attribute on the scoped parent.  That is the whole point:
    P2 loads three importers and the first one warms the attribute, so P2 passes
    with ``psi_layer_<layer>`` unregistered and cannot witness the scaffolding it
    claims to cover.  Deleting ``_ensure_scope_package`` reddens this criterion
    with ``ModuleNotFoundError: No module named 'psi_layer_official'``.

    Both shapes ship today: ``from _pkg import sub`` at 6 sites under
    ``agents/feishu/tools/``, in-package relative imports at 13 sites under
    ``agents/desktop/tools/_fusion_memory/``.
    """
    build, stem, expected = _P6_SHAPES[shape]
    official = build(tmp_path, "official", "LAYER-OFFICIAL")
    personal = build(tmp_path, "personal", "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    for layer_id in ("official", "personal"):
        seen = modules[f"{layer_id}/{stem}"].SEEN
        assert seen == f"LAYER-{layer_id.upper()}{expected}", (
            f"{layer_id}: the parent package did not resolve to this layer's own file, got {seen!r}"
        )


# ── coexistence with the kernel's existing stash/restore ──────────────────────


def test_a1_leaves_the_kernel_stash_empty(tmp_path: Path) -> None:
    """A1 and the existing stash/restore do not fight: the stash stays empty.

    ``_stash_private_modules`` collects plain ``sys.modules`` entries whose
    ``__file__`` sits directly in the closing dir.  Under A1 the private
    modules are registered under scoped names and the plain alias is already
    gone by the time a scope closes, so there is nothing for it to find.  It
    becomes dead weight rather than a conflicting second mechanism — which
    answers "must stash/restore be removed first": no, but it stops doing
    anything.
    """
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")

    _exec_layers_together([official, personal])

    assert _stashed_under(tmp_path) == {}, f"the kernel stash captured A1-scoped modules: {_stashed_under(tmp_path)}"


# ── mutation red-check ────────────────────────────────────────────────────────

# Each knob must break the criteria it is responsible for and leave the others
# alone; a knob that reddens everything would mean the criteria shadow each
# other and cannot localise a regression.  Recorded as executable assertions
# rather than a commit-message claim so the mapping cannot silently rot.


def _p1_holds(tmp_path: Path) -> bool:
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    return modules["official/probe"].SEEN == "LAYER-OFFICIAL" and modules["personal/probe"].SEEN == "LAYER-PERSONAL"


def _p3_holds(tmp_path: Path) -> bool:
    official, personal = _derivation_layers(tmp_path)
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    return modules["personal/probe"].SEEN == "FROM-OFFICIAL:OFFICIAL-CORE"


def _p4_holds(tmp_path: Path) -> bool:
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")
    _exec_layers_together([official, personal])
    return _private_modules_under(tmp_path) == [] and _stashed_under(tmp_path) == {}


def _p5_holds(tmp_path: Path) -> bool:
    official = _bare_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _bare_layer(tmp_path, "personal", "LAYER-PERSONAL")
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    return modules["official/probe"].HELPER is modules["official/probe_second"].HELPER


def _p2_holds(tmp_path: Path) -> bool:
    official = _dotted_layer(tmp_path, "official", "LAYER-OFFICIAL")
    personal = _dotted_layer(tmp_path, "personal", "LAYER-PERSONAL")
    try:
        modules = _exec_layers_together([official, personal])
    except Exception:
        return False
    for layer in ("official", "personal"):
        for importer in ("probe", "probe_from_pkg", "probe_import_dotted"):
            seen = modules[f"{layer}/{importer}"].SEEN
            if seen != f"LAYER-{layer.upper()}-sub":
                return False
    return True


def _p6_holds(tmp_path: Path) -> bool:
    for shape, (build, stem, expected) in _P6_SHAPES.items():
        root = tmp_path / shape
        layers = [build(root, "official", "LAYER-OFFICIAL"), build(root, "personal", "LAYER-PERSONAL")]
        try:
            modules = _exec_layers_together(layers)
        except Exception:
            return False
        for layer_id in ("official", "personal"):
            seen = modules[f"{layer_id}/{stem}"].SEEN
            if seen != f"LAYER-{layer_id.upper()}{expected}":
                return False
    return True


CRITERIA = {
    "P1": _p1_holds,
    "P2": _p2_holds,
    "P3": _p3_holds,
    "P4": _p4_holds,
    "P5": _p5_holds,
    "P6": _p6_holds,
}


@contextmanager
def _sandboxed() -> Iterator[None]:
    """Restore import globals around one criterion run.

    Load-bearing, and found by the mutation check failing for the wrong reason:
    with the criteria sharing one process, a plain-name module leaked by the
    *first* criterion satisfied the second one's import straight from
    ``sys.modules``, so the hook was never consulted and no residue was created
    under that criterion's own tmp dir.  P4 came out green while the leak it
    exists to catch was happening one directory over.
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


# knob → the criteria it must redden.  Anything not listed must stay green.
#
# The measured mapping, not a predicted one.  ``leak_plain_aliases`` reddens the
# isolation criteria as well as the residue one, and that is kept rather than
# split: a plain name left bound *is* the isolation failure (the next layer's
# import finds it in ``sys.modules`` and never reaches the hook), the residue is
# the same defect seen from the other side.
#
# No knob reddens everything, and P4/P5/P6 are each the sole witness for one
# knob, so a regression in any of them is localisable.  P1 and P2 always move
# together here — they differ in *shape* (bare vs package), not in the behaviour
# that resolves them.
#
# Two entries are deliberately **empty**, and that is the finding rather than an
# omission: ``ignore_layer_priority`` and ``reverse_layer_priority`` break nothing
# in this file.  Every fixture here has two layers, where "the layers other than
# the asker" is a set of one and no ordering of it is distinguishable.  Their
# witness is R5 in the sibling file, on three layers.  Listing them with an empty
# set keeps that measured — if a two-layer criterion ever did start depending on
# priority order, this assertion would catch it.
#
# ``ignore_asking_layer`` reddens P3, which it did **not** before priority
# sorting existed, and the change is instructive: P3 opens its layers
# ``[official, personal]``, so with the old open-order search the official
# helper's own ``import _layer_core`` happened to find official's copy first and
# P3 passed for a reason unrelated to the behaviour it names.  Now the ranked
# order puts personal first and the knob is visible.  P3's pass no longer rests
# on which layer was opened first.
#
# ``priority_over_asking_layer`` reddens P1, P2, P3 and P6 — measured, and the
# evidence behind R7's decision: dropping "the asking layer answers for itself"
# in favour of pure priority does not merely reorder fallback, it dismantles the
# per-layer isolation P1/P2 exist to assert.
#
# P6 moves with the isolation knobs *and* is the sole witness for
# ``no_scope_package``.  It exists because the earlier claim recorded here — that
# P2's three import forms pin the dotted-specific scaffolding — was wrong:
# measured, deleting ``_ensure_scope_package`` left all 19 of the original
# criteria green, because P2's first-loaded importer warms the parent attribute
# that the ``from _pkg import sub`` form would otherwise have to resolve.
# ``no_module_reuse`` does *not* redden P6: with the scope package registered,
# re-exec'ing a helper still resolves the parent correctly.
EXPECTED_BREAKAGE = {
    "drop_layer_prefix": {"P1", "P2", "P6"},
    "ignore_asking_layer": {"P1", "P2", "P3", "P6"},
    "no_cross_layer_fallback": {"P3"},
    "no_module_reuse": {"P5"},
    "leak_plain_aliases": {"P1", "P2", "P4", "P6"},
    "no_scope_package": {"P6"},
    "ignore_layer_priority": set(),
    "reverse_layer_priority": set(),
    "priority_over_asking_layer": {"P1", "P2", "P3", "P6"},
}


@pytest.mark.parametrize("knob", sorted(EXPECTED_BREAKAGE))
def test_mutations_redden_exactly_their_own_criteria(tmp_path: Path, knob: str) -> None:
    """Disabling one hook behaviour must break its criteria and only those."""
    expected = EXPECTED_BREAKAGE[knob]
    broken = set()
    with mutated(knob):
        for name, check in CRITERIA.items():
            with _sandboxed():
                if not check(tmp_path / name):
                    broken.add(name)
    assert broken == expected, f"mutation {knob!r} broke {sorted(broken)}, expected exactly {sorted(expected)}"
