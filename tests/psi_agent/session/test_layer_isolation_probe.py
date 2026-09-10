"""Private-module isolation when two content layers are loaded together.

``tool_registry``'s private-module scoping (``_tools_dir_on_sys_path`` plus
the stash/restore pair) assumes *one* tools dir is in scope at a time: a
dir's private modules are stashed out of ``sys.modules`` when its scope
ends and restored when it reopens.  Content layering (official / enterprise
/ personal tool dirs all live at once) breaks that assumption, so these
probes pin down what happens today.

The layers are loaded by a deliberately minimal shell in this file whose
only job is "open both layers' import scopes, then exec them layer by
layer".  ``ToolRegistry`` itself is untouched: the point is to document the
hazard, not to model a fix.  Resolution is judged by reading a ``MARKER``
value back out of the exec'd tool module, never by inspecting
``sys.modules`` internals, so the probes stay valid across whatever fix
lands.
"""

from __future__ import annotations

import sys
import textwrap
import types
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from psi_agent.session import tool_registry
from psi_agent.session.tool_registry import _tools_dir_on_sys_path

# ── the two-layer loading shell ───────────────────────────────────────────────


def _exec_layers_together(layer_dirs: list[Path]) -> dict[str, types.ModuleType]:
    """Open every layer's import scope at once, then exec layer by layer.

    This is the shape content layering forces: while the personal layer's
    files are exec'd, the official layer is still in scope (that is what
    makes cross-layer derivation possible at all).  Contrast with loading
    one dir, closing its scope, then loading the next — the sequential case
    already covered by ``test_two_workspaces_bind_their_own_private_helper``.

    The shell does nothing else: no per-layer ``sys.path`` re-prioritising,
    no ``sys.modules`` bookkeeping.  Anything that would isolate the layers
    belongs in ``tool_registry``, not here.  Probes that care about
    collisions run this in both layer orders, so a result cannot be an
    artefact of which dir happened to land at the front of ``sys.path``.

    Returns ``{"<layer>/<file stem>": module}`` for every exec'd tool file.
    """
    modules: dict[str, types.ModuleType] = {}
    with ExitStack() as stack:
        for layer_dir in layer_dirs:
            stack.enter_context(_tools_dir_on_sys_path(layer_dir))
        for layer_dir in layer_dirs:
            modules.update(_exec_layer_files(layer_dir))
    return modules


def _ordered(official: Path, personal: Path, order: str) -> list[Path]:
    """The two layer dirs, in the requested load order."""
    return [official, personal] if order == "official-first" else [personal, official]


BOTH_ORDERS = pytest.mark.parametrize("order", ["official-first", "personal-first"])


def _exec_layer_files(tools_dir: Path) -> dict[str, types.ModuleType]:
    """Compile + exec one layer's public tool files; caller owns the scope."""
    out: dict[str, types.ModuleType] = {}
    layer = tools_dir.parent.name
    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"psi_probe_{layer}_{py_file.stem}"
        module = types.ModuleType(module_name)
        module.__file__ = str(py_file)
        sys.modules[module_name] = module
        exec(compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec"), module.__dict__)
        out[f"{layer}/{py_file.stem}"] = module
    return out


# ── layer fixtures on disk ────────────────────────────────────────────────────


def _write_bare_layer(tools_dir: Path, marker: str) -> None:
    """A layer with a bare-name private helper and a tool that imports it."""
    tools_dir.mkdir(parents=True)
    (tools_dir / "_layer_helper.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (tools_dir / "probe.py").write_text(
        textwrap.dedent(
            """
            from _layer_helper import MARKER as SEEN
            """
        ),
        encoding="utf-8",
    )


def _write_dotted_layer(tools_dir: Path, marker: str) -> None:
    """A layer whose private helper lives in a dotted package (``_pkg.sub``).

    This is the shape ``agents/feishu/tools/_feishu/`` ships (15 modules).
    """
    pkg = tools_dir / "_layer_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (tools_dir / "probe.py").write_text(
        textwrap.dedent(
            """
            from _layer_pkg.sub import MARKER as SEEN
            """
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _isolate_import_state() -> Iterator[None]:
    """Undo every global this probe touches, so probes cannot leak into each other.

    Teardown runs after the test body, so the side-effect probe can still
    observe residue before it is cleaned up.
    """
    path_before = list(sys.path)
    modules_before = dict(sys.modules)
    depth_before = dict(tool_registry._path_scope_depth)
    stash_before = dict(tool_registry._private_module_stash)
    try:
        yield
    finally:
        sys.path[:] = path_before
        for name in set(sys.modules) - set(modules_before):
            del sys.modules[name]
        sys.modules.update(modules_before)
        tool_registry._path_scope_depth.clear()
        tool_registry._path_scope_depth.update(depth_before)
        tool_registry._private_module_stash.clear()
        tool_registry._private_module_stash.update(stash_before)


# ── P1 / P2 — same-named private module in two layers ─────────────────────────


@BOTH_ORDERS
@pytest.mark.xfail(
    strict=True,
    reason=(
        "must fail on current code: both layers are in scope at once, so the "
        "bare-name helper resolves to whichever dir sits earlier on sys.path "
        "and the stash/restore pair never runs between layers. When layering "
        "is fixed this becomes XPASS, strict=True turns that red, and the "
        "xfail marker should then be deleted."
    ),
)
def test_p1_bare_name_helper_resolves_per_layer(tmp_path: Path, order: str) -> None:
    """P1: each layer's tool must bind its *own* bare-name private helper.

    Run in both layer orders: one layer always wins both lookups, so
    whichever order runs, one of the two assertions fails.
    """
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _write_bare_layer(official, "LAYER-OFFICIAL")
    _write_bare_layer(personal, "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    assert modules["official/probe"].SEEN == "LAYER-OFFICIAL", (
        "official layer bound the personal layer's private helper"
    )
    assert modules["personal/probe"].SEEN == "LAYER-PERSONAL", (
        "personal layer bound the official layer's private helper"
    )


@BOTH_ORDERS
@pytest.mark.xfail(
    strict=True,
    reason=(
        "must fail on current code, same collision as P1 plus a second "
        "defect: tool_registry.py:106 skips every module name containing a "
        "dot, so dotted private packages (agents/feishu/tools/_feishu/, 15 "
        "modules) are never stashed and leak across dirs even sequentially. "
        "When layering is fixed this becomes XPASS, strict=True turns that "
        "red, and the xfail marker should then be deleted."
    ),
)
def test_p2_dotted_helper_resolves_per_layer(tmp_path: Path, order: str) -> None:
    """P2: same as P1 but the private helper is a dotted package (``_pkg.sub``)."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _write_dotted_layer(official, "LAYER-OFFICIAL")
    _write_dotted_layer(personal, "LAYER-PERSONAL")

    modules = _exec_layers_together(_ordered(official, personal, order))

    assert modules["official/probe"].SEEN == "LAYER-OFFICIAL", (
        "official layer bound the personal layer's private package"
    )
    assert modules["personal/probe"].SEEN == "LAYER-PERSONAL", (
        "personal layer bound the official layer's private package"
    )


# ── P3 / P4 — a personal-layer tool deriving from the official layer ──────────


def test_p3_personal_tool_imports_official_bare_helper(tmp_path: Path) -> None:
    """P3: a personal tool importing an official-layer private helper works.

    The official layer's own tools deliberately do *not* import the helper,
    so this only passes if the official dir is genuinely in scope while the
    personal layer is exec'd — not because ``sys.modules`` happened to be
    warm.
    """
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    official.mkdir(parents=True)
    (official / "_official_only.py").write_text("MARKER = 'FROM-OFFICIAL'\n", encoding="utf-8")
    (official / "probe.py").write_text("SEEN = 'official-tool'\n", encoding="utf-8")
    personal.mkdir(parents=True)
    (personal / "probe.py").write_text(
        textwrap.dedent(
            """
            from _official_only import MARKER as SEEN
            """
        ),
        encoding="utf-8",
    )

    modules = _exec_layers_together([official, personal])

    assert modules["personal/probe"].SEEN == "FROM-OFFICIAL"


def test_p4_personal_tool_imports_official_dotted_helper(tmp_path: Path) -> None:
    """P4: same derivation as P3, with the official helper in a dotted package."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    pkg = official / "_official_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub.py").write_text("MARKER = 'FROM-OFFICIAL-DOTTED'\n", encoding="utf-8")
    (official / "probe.py").write_text("SEEN = 'official-tool'\n", encoding="utf-8")
    personal.mkdir(parents=True)
    (personal / "probe.py").write_text(
        textwrap.dedent(
            """
            from _official_pkg.sub import MARKER as SEEN
            """
        ),
        encoding="utf-8",
    )

    modules = _exec_layers_together([official, personal])

    assert modules["personal/probe"].SEEN == "FROM-OFFICIAL-DOTTED"


# ── side effects ──────────────────────────────────────────────────────────────


def test_layered_load_restores_sys_path_and_clears_bare_modules(tmp_path: Path) -> None:
    """``sys.path`` is restored and bare-name experiment modules do not linger."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _write_bare_layer(official, "LAYER-OFFICIAL")
    _write_bare_layer(personal, "LAYER-PERSONAL")

    path_before = list(sys.path)
    _exec_layers_together([official, personal])

    assert sys.path == path_before
    leaked = _private_modules_under(tmp_path)
    assert leaked == [], f"bare-name private modules left in sys.modules: {leaked}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "must fail on current code: tool_registry.py:106 skips names "
        "containing a dot, so _layer_pkg.sub is never stashed and stays in "
        "sys.modules after every scope closes — this is the residue that "
        "makes P2 leak. Becomes XPASS once dotted names are stashed too."
    ),
)
def test_layered_load_clears_dotted_modules(tmp_path: Path) -> None:
    """Dotted private submodules must not survive the layered load either."""
    official = tmp_path / "official" / "tools"
    personal = tmp_path / "personal" / "tools"
    _write_dotted_layer(official, "LAYER-OFFICIAL")
    _write_dotted_layer(personal, "LAYER-PERSONAL")

    _exec_layers_together([official, personal])

    leaked = _private_modules_under(tmp_path)
    assert leaked == [], f"dotted private modules left in sys.modules: {leaked}"


def _private_modules_under(root: Path) -> list[str]:
    """Names of ``_``-prefixed ``sys.modules`` entries whose file is under *root*."""
    found: list[str] = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith("_"):
            continue
        origin = getattr(mod, "__file__", None)
        if not origin:
            continue
        try:
            if root in Path(origin).resolve().parents:
                found.append(name)
        except OSError:
            continue
    return sorted(found)
