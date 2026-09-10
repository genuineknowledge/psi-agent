"""Per-layer private-module isolation for content layering (design A1).

Tool files import their same-directory private helpers by bare name
(``import _runtime_paths``), and ``sys.modules`` has exactly one slot per bare
name.  With a single tools dir in scope at a time that is merely fragile —
``tool_registry``'s stash/restore pair papers over it.  Content layering
(official / enterprise / personal tool dirs all live at once) removes the "one
at a time" premise, and two layers shipping a same-named helper then contend
for one slot: whichever dir sits earlier on ``sys.path`` answers for both.

A1 makes that collision impossible rather than avoiding it.  Each layer loads
its private modules under a *scoped* name, ``psi_layer_<token>.<plain>``, so
two layers hold two keys and neither can overwrite the other.  Tool files keep
writing ``import _helper``; a ``sys.meta_path`` finder decides which layer's
file that resolves to, based on which layer is *asking*.

Four things this needs that the design sketch does not show, each found by
measurement rather than by reading CPython — see
``docs/superpowers/specs/2026-09-10-session2-a1-validation-delivery.md``:

1. The plain name has to be bound while an import is in flight, because
   CPython resolves a dotted child by looking its parent up under the *plain*
   name.  The binding is transient, released when the tool file that triggered
   it finishes.  Binding permanently is the original hazard back again: the
   next file's ``import _helper`` would hit ``sys.modules`` and the finder
   would never be consulted.
2. ``psi_layer_<token>`` itself has to exist in ``sys.modules`` as a package.
   The trigger is whether the parent gets reached *as a package* rather than as
   an already-bound attribute — not nesting depth.
3. The reuse path (helper already loaded) must re-bind the plain name too, or
   a layer's second dotted importer fails where the first succeeded — a
   failure that only shows up once the cache is warm.
4. Search order follows ``Layer.priority``, never the order the layers were
   opened in.  Open order is glob order in production, which no layer controls.

Resolution order is **the asking layer first, then the rest by descending
priority**.  Priority orders the cross-layer fallback only; it never overrides
the asking layer's own helper.  A ``_``-prefixed helper is a layer's
implementation, not its addressable surface, so a downstream layer that
happens to reuse a helper name must not displace an upstream layer's copy —
that collision is the whole hazard.  The addressable surface is the public
tool name, where ``ToolRegistry`` applies last-wins instead.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import re
import sys
import threading
import types
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path

from loguru import logger

SCOPE_PREFIX = "psi_layer_"

# ``layer_id`` is a tools-dir path in production (see ``tool_registry._layer_id``),
# and a path is not usable inside a dotted module name: separators and dots would
# make CPython read it as further package nesting.  So the scope segment is a
# sanitised stem plus a hash of the full id — readable in a traceback, and still
# one distinct segment per layer, which is the property the isolation rests on.
_UNSAFE_IN_MODULE_NAME = re.compile(r"[^0-9A-Za-z_]+")


@dataclass(frozen=True)
class Layer:
    """One content layer: its id, its tools dir, and its rank.

    ``priority`` is what search order follows — **higher wins**, so the layer
    closest to the user outranks the ones behind it.  It is an explicit field
    rather than the caller's list order because list order is *open* order (glob
    order in production, which no layer controls), and rather than something
    inferred from ``layer_id`` because the real ids are tools-dir paths, not the
    words "official"/"personal".  Both shortcuts were the bug this field exists
    to remove; keeping either as a fallback would relocate it rather than fix
    it.  Whoever assembles the layers knows their ranks and states them here.

    Ties keep their relative open order (``sorted`` is stable) — the one place
    open order still decides anything, and only because the assembler declared
    the two layers equal.
    """

    layer_id: str
    tools_dir: Path
    priority: int

    @property
    def scope_token(self) -> str:
        """The single module-name segment standing for this layer."""
        return _scope_token(self.layer_id)


def _scope_token(layer_id: str) -> str:
    """A module-name-safe segment that is unique per ``layer_id``.

    The hash carries uniqueness (two dirs whose names sanitise to the same stem
    must not collide); the stem is there so ``psi_layer_tools_1f4a2b`` in a
    traceback still says which layer it came from.
    """
    digest = hashlib.sha256(layer_id.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    stem = _UNSAFE_IN_MODULE_NAME.sub("_", Path(layer_id).name).strip("_")
    return f"{stem}_{digest}" if stem else digest


def scope_package_name(layer_id: str) -> str:
    """``psi_layer_<token>`` — the synthetic parent package for one layer."""
    return f"{SCOPE_PREFIX}{_scope_token(layer_id)}"


def scoped_module_name(layer_id: str, plain: str) -> str:
    """The name *plain* is registered under while loaded for *layer_id*."""
    return f"{scope_package_name(layer_id)}.{plain}"


# ── which layer is asking ─────────────────────────────────────────────────────

# A ``ContextVar`` rather than an attribute because it has to follow *nested*
# imports: when a personal tool pulls in an official helper, that helper's own
# ``import _core`` must resolve against the official layer, not the personal one
# that started the chain.  Also keeps concurrent loads in separate tasks from
# reading each other's asker.
_asking_layer: ContextVar[str] = ContextVar("psi_asking_layer")


@contextmanager
def asking_layer(layer_id: str) -> Iterator[None]:
    """Pin *layer_id* as the layer whose code is executing."""
    token = _asking_layer.set(layer_id)
    try:
        yield
    finally:
        _asking_layer.reset(token)


def _find_source(tools_dir: Path, fullname: str) -> tuple[Path, list[str] | None] | None:
    """Locate *fullname* under *tools_dir* as ``(file, submodule search dirs)``.

    Handles both shapes the repo ships: a bare module (``_runtime_paths.py``)
    and a package (``_feishu/`` with 15 submodules, imported as ``_feishu.auth``).
    """
    *parents, last = fullname.split(".")
    base = tools_dir
    for part in parents:
        base = base / part
    try:
        package_init = base / last / "__init__.py"
        if package_init.is_file():
            return package_init, [str(base / last)]
        module_file = base / f"{last}.py"
        if module_file.is_file():
            return module_file, None
    except OSError:
        return None
    return None


class _ScopedLoader(importlib.abc.Loader):
    """Executes a layer's private module with that layer pinned as the asker.

    Also binds the module's *plain* name for as long as the import is in flight
    (scaffolding 1): CPython resolves a dotted child by looking its parent up
    under the plain name, so without this ``import _pkg.sub`` raises
    ``KeyError: '_pkg'``.  ``LayerImportHook.release_aliases`` drops it once the
    tool file finishes, so two layers are never simultaneously reachable under
    one plain name.
    """

    def __init__(self, inner: importlib.abc.Loader, plain: str, layer_id: str, hook: LayerImportHook) -> None:
        self._inner = inner
        self._plain = plain
        self._layer_id = layer_id
        self._hook = hook

    def create_module(self, spec: ModuleSpec) -> types.ModuleType | None:
        return self._inner.create_module(spec)

    def exec_module(self, module: types.ModuleType) -> None:
        self._hook.bind_alias(self._plain, module)
        with asking_layer(self._layer_id):
            self._inner.exec_module(module)


class _ReuseLoader(importlib.abc.Loader):
    """Hands back an already-exec'd module instead of running it a second time.

    Renaming must not become re-executing: ``_background_process_registry``
    keeps its live-process table in module globals, and a second module object
    would split that table so the layer's second tool could not see the first
    tool's background processes.
    """

    def __init__(self, module: types.ModuleType, plain: str, hook: LayerImportHook) -> None:
        self._module = module
        self._plain = plain
        self._hook = hook

    def create_module(self, spec: ModuleSpec) -> types.ModuleType | None:
        return self._module

    def exec_module(self, module: types.ModuleType) -> None:
        # Scaffolding 3: re-bind the plain name on the reuse path too.  A dotted
        # child's parent lookup needs it whether or not the parent was already
        # loaded, so skipping it here fails the layer's *second* dotted importer
        # while the first one succeeded.
        self._hook.bind_alias(self._plain, module)


class LayerImportHook(importlib.abc.MetaPathFinder):
    """Resolves bare-name private imports to the asking layer's own file.

    Installed on ``sys.meta_path`` for a set of layers by ``layers_open``.  Only
    claims names whose first segment starts with ``_``; everything else
    (``anyio``, ``psi_agent.*``) falls through to the normal machinery
    untouched, and so does anything already inside a layer scope.
    """

    def __init__(self, layers: Sequence[Layer]) -> None:
        self._layers = list(layers)
        self._aliases: set[str] = set()
        self._lock = threading.Lock()

    def _ranked_layers(self) -> list[Layer]:
        """Every layer, most-specific first, by ``Layer.priority``.

        Scaffolding 4.  Sorting rather than trusting ``self._layers`` is the
        point: the constructor is handed layers in *open* order, so trusting it
        would make resolution depend on which layer happened to be opened
        first.  Two layers cannot show this — "the layers other than the asker"
        is a set of one and every ordering of it agrees — which is why it took a
        third layer to surface.
        """
        return sorted(self._layers, key=lambda layer: layer.priority, reverse=True)

    def _search_order(self) -> list[Layer]:
        """The asking layer first, then the remaining layers by descending priority.

        Two rules, and only the second is about precedence:

        * **The asking layer's own file always wins**, even when a
          higher-priority layer ships the same name.  This is the isolation A1
          exists to provide, not a preference among equals: measured, ranking
          priority ahead of the asker dismantles it rather than merely
          reordering fallback.
        * **Priority orders the fallback**: a name the asking layer does not
          ship is looked for most-specific-layer-first, so personal beats
          enterprise beats official.
        """
        ranked = self._ranked_layers()
        asking = _asking_layer.get(None)
        if asking is None:
            return ranked
        own = [layer for layer in ranked if layer.layer_id == asking]
        return own + [layer for layer in ranked if layer.layer_id != asking]

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> ModuleSpec | None:
        if fullname.startswith(SCOPE_PREFIX) or not fullname.partition(".")[0].startswith("_"):
            return None
        for layer in self._search_order():
            found = _find_source(layer.tools_dir, fullname)
            if found is None:
                continue
            source, search_locations = found
            self._ensure_scope_package(layer.layer_id)
            scoped = scoped_module_name(layer.layer_id, fullname)
            cached = self._cached_module(scoped)
            if cached is not None:
                return importlib.util.spec_from_loader(scoped, _ReuseLoader(cached, fullname, self))
            spec = importlib.util.spec_from_file_location(scoped, source, submodule_search_locations=search_locations)
            if spec is None or spec.loader is None:
                return None
            spec.loader = _ScopedLoader(spec.loader, fullname, layer.layer_id, self)
            return spec
        return None

    def _cached_module(self, scoped: str) -> types.ModuleType | None:
        """The already-exec'd module for *scoped*, if this layer loaded it before.

        A separate method so a test can suppress reuse and watch the criterion
        that depends on it (one module object per layer) go red — the alternative
        is a module-level knob shipping in production code.
        """
        return sys.modules.get(scoped)

    def _ensure_scope_package(self, layer_id: str) -> None:
        """Register ``psi_layer_<token>`` as a package (scaffolding 2).

        Needed whenever CPython resolves the parent package's own module object
        through the normal machinery, which walks up to ``psi_layer_<token>``
        and raises ``ModuleNotFoundError`` if that name is absent.  Both shapes
        the repo ships reach it: ``from _pkg import sub`` (6 sites under
        ``agents/feishu/tools/``) and in-package relative imports (``from .
        import sibling``, 13 sites in ``agents/desktop/tools/_fusion_memory/``).

        Nesting depth is *not* the trigger — ``_pkg/sub/deeper.py`` resolves
        without this, because each level is bound by ``bind_alias``.  What
        matters is whether the parent is reached as a package rather than as an
        already-bound attribute.
        """
        name = scope_package_name(layer_id)
        if name in sys.modules:
            return
        package = types.ModuleType(name)
        # Namespace-style: children are placed here by us, never found on disk.
        package.__path__ = []
        sys.modules[name] = package

    def bind_alias(self, plain: str, module: types.ModuleType) -> None:
        """Bind *plain* transiently, for the duration of the current tool file."""
        with self._lock:
            sys.modules[plain] = module
            self._aliases.add(plain)

    def release_aliases(self) -> None:
        """Drop every transient plain-name binding taken during one tool file."""
        with self._lock:
            for name in self._aliases:
                sys.modules.pop(name, None)
            self._aliases.clear()


# ── opening a set of layers ───────────────────────────────────────────────────

# The hook installed for the current load, so ``executing_tool_file`` can find it
# without every caller threading it through.  A ``ContextVar`` for the same
# reason as ``_asking_layer``: concurrent loads must not see each other's hook.
_active_hook: ContextVar[LayerImportHook | None] = ContextVar("psi_active_layer_hook", default=None)


@contextmanager
def layers_open(layers: Sequence[Layer]) -> Iterator[LayerImportHook]:
    """Install a resolving hook for *layers*, and tear it back down.

    Leaves ``sys.modules`` as it found it: the scoped modules and the synthetic
    ``psi_layer_<token>`` parents share one prefix, so a single sweep clears
    both.  Dropping them matters because the scoped name is derived from the
    tools-dir path, which is stable across loads — a survivor would be handed to
    the next load of the same dir even after the file on disk changed.
    """
    hook = LayerImportHook(layers)
    sys.meta_path.insert(0, hook)
    token = _active_hook.set(hook)
    try:
        yield hook
    finally:
        _active_hook.reset(token)
        with suppress(ValueError):
            sys.meta_path.remove(hook)
        hook.release_aliases()
        for name in [n for n in sys.modules if n.startswith(SCOPE_PREFIX)]:
            del sys.modules[name]
        logger.debug(f"Layer import hook removed for {len(layers)} layer(s)")


@contextmanager
def executing_tool_file(layer_id: str) -> Iterator[None]:
    """Run one tool file with *layer_id* asking, releasing its aliases after.

    The release is per *file*, not per load: that is what makes the next file's
    imports resolve against its own layer instead of inheriting the previous
    file's plain-name bindings.  A no-op when no hook is installed, so the
    single-dir path can call it unconditionally.
    """
    hook = _active_hook.get()
    with asking_layer(layer_id):
        try:
            yield
        finally:
            if hook is not None:
                hook.release_aliases()
