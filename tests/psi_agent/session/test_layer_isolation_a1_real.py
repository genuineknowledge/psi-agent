"""A1 on the shapes the repo actually ships: real private modules, three layers.

``test_layer_isolation_a1_probe`` answered "is A1 buildable" on layers built in
``tmp_path``: two of them, private helpers written by the test, no third-party
imports anywhere.  Both simplifications hide things.

* Synthetic helpers import only each other.  The real ones mix ``psi_agent.*``,
  ``lark_channel``, ``anyio`` and ``loguru`` into the same file.  A1's hook
  claims only names whose first segment starts with ``_`` and lets everything
  else fall through — a *claim* that is cheap to state and was never measured
  against a file that actually has both kinds of import in it.
* Two layers cannot express layer *precedence*.  With one "other" layer, any
  fallback lands in the only place it can.  The shipping shape is three layers
  (official / enterprise / personal) where a miss in personal must prefer
  enterprise over official, and that ordering is exactly what two layers cannot
  distinguish.

The verdicts here (R1-R5) are read back out of real modules where the subject is
real-module behaviour, and out of ``tmp_path`` layers where the subject is
multi-layer resolution.  Which one a criterion uses is stated in its docstring,
because a criterion that claims to measure real modules while actually reading a
synthetic copy is the failure mode this file exists to avoid.

Nothing under ``agents/`` is written to.  R3 copies the real ``_feishu``
package into ``tmp_path`` to get two *distinguishable* copies of it; the
originals are only ever read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from psi_agent.session import tool_registry

from .test_layer_isolation_a1_probe import (
    LAYER_PRIORITY,
    MUTATIONS,
    Layer,
    _exec_layers_together,
    _scoped_name,
    make_layer,
    mutated,
)

# ── the real thing on disk ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FEISHU_TOOLS = _REPO_ROOT / "agents" / "feishu" / "tools"
_FEISHU_PKG = _FEISHU_TOOLS / "_feishu"
_DESKTOP_TOOLS = _REPO_ROOT / "agents" / "desktop" / "tools"

# The 15 domain modules, discovered rather than listed: a module added to the
# package must be covered here without anyone remembering to update a constant.
_FEISHU_MODULES = sorted(p.stem for p in _FEISHU_PKG.glob("*.py") if p.stem != "__init__")

# ``_feishu_impl`` re-exports these 12 and they import it back at module scope,
# so each only imports cleanly once ``_feishu_impl`` is in flight.  Measured, and
# identical to ``tests/agents/feishu/test_feishu_tool_import_cycles.py``'s
# ``_KNOWN_CYCLIC``: the cycles are the repo's, not something A1 introduces.
_KNOWN_CYCLIC = frozenset(
    {
        "approval",
        "attendance",
        "auth",
        "bitable",
        "contact",
        "doc",
        "drive",
        "leave",
        "message",
        "sheet",
        "task",
        "worktree",
    }
)

# Third-party and first-party names the real modules pull in.  The hook must
# leave every one of these to the normal machinery.
_MUST_NOT_BE_CLAIMED = ("anyio", "loguru", "yaml", "lark_channel", "psi_agent", "typing", "json")


def _desktop_private_helpers() -> list[str]:
    """Top-level ``_``-prefixed helpers under ``agents/desktop/tools``.

    Counted from disk, not asserted to a number: this is ToC code that this card
    may only read, and pinning a count here would make an unrelated ToC change
    fail a session test.
    """
    return sorted(p.stem for p in _DESKTOP_TOOLS.glob("_*.py"))


# ── R1 / R2 run in a child interpreter ────────────────────────────────────────
#
# One process cannot answer "does this module import standalone": the criteria
# share ``sys.modules``, so a module left warm by an earlier import satisfies a
# later one and the cycle stays invisible.  Measured, not assumed — importing
# all 15 in one process reports 7 OK / 8 failed, while importing each in its own
# interpreter reports 3 OK / 12 failed.  The 7 was an artefact of warm state.

_CHILD_PREAMBLE = """
import sys, importlib.util, json
from pathlib import Path
ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location(
    "a1probe", ROOT / "tests/psi_agent/session/test_layer_isolation_a1_probe.py"
)
a1 = importlib.util.module_from_spec(_spec)
sys.modules["a1probe"] = a1
_spec.loader.exec_module(a1)
TOOLS = (ROOT / "agents/feishu/tools").resolve()
LAYER = a1.make_layer("official", TOOLS)
"""


def _run_child(body: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Exec *body* in a fresh interpreter with the A1 prototype importable.

    ``cwd`` is the repo root and ``PYTHONPATH`` carries ``src`` so the child
    resolves ``psi_agent`` from this worktree rather than from whatever checkout
    happens to own the installed ``.pth``.
    """
    script = _CHILD_PREAMBLE + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script, str(_REPO_ROOT), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
    )


def _import_real_module_under_a1(module: str, impl_first: bool) -> subprocess.CompletedProcess[str]:
    """Import ``_feishu.<module>`` through the A1 hook, in its own interpreter.

    *impl_first* picks the load order: ``tool_registry`` reaches ``_feishu_impl``
    first in production (glob order puts ``_feishu_api_impl``/``_feishu_impl``
    ahead of the tool files that need a domain module), while ``impl_first=False``
    is the standalone order that the cycle fix is about.
    """
    return _run_child(
        f"""
        target = {module!r}
        impl_first = {impl_first!r}
        with a1._a1_layers_open([LAYER]) as hook:
            token = a1._asking_layer.set("official")
            try:
                if impl_first:
                    __import__("_feishu_impl")
                    hook.release_aliases()
                __import__("_feishu." + target)
                scoped = sys.modules["psi_layer_official._feishu." + target]
                misclaimed = sorted(
                    n for n in sys.modules
                    if n.startswith("psi_layer_") and not n.split(".", 1)[-1].startswith("_")
                    and n != "psi_layer_official"
                )
                print("VERDICT " + json.dumps({{
                    "ok": True,
                    "name": scoped.__name__,
                    "file": scoped.__file__,
                    "misclaimed": misclaimed,
                    "third_party_normal": [
                        n for n in {_MUST_NOT_BE_CLAIMED!r}
                        if any(k == n or k.startswith(n + ".") for k in sys.modules)
                    ],
                }}))
            except Exception as exc:
                print("VERDICT " + json.dumps({{
                    "ok": False, "error": type(exc).__name__, "message": str(exc)[:200],
                }}))
            finally:
                a1._asking_layer.reset(token)
                hook.release_aliases()
        """
    )


def _verdict(proc: subprocess.CompletedProcess[str]) -> dict:
    """The child's single JSON verdict line, or a failure describing the crash."""
    for line in proc.stdout.splitlines():
        if line.startswith("VERDICT "):
            return json.loads(line[len("VERDICT ") :])
    return {"ok": False, "error": "NoVerdict", "message": proc.stderr[-400:]}


# ── global-state isolation ────────────────────────────────────────────────────


@contextmanager
def _real_sandboxed() -> Iterator[None]:
    """Undo the import globals one criterion touched, before the next one runs.

    The same trap the sibling probe documents: the criteria share a process, so a
    plain-name module left behind by one satisfies the next one's import straight
    out of ``sys.modules``.  The hook is then never consulted and the criterion
    reports green without having exercised anything.
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


@pytest.fixture(autouse=True)
def _isolate_real_probe_state() -> Iterator[None]:
    """Restore every global this file mutates, mutation knobs included."""
    mutations_before = dict(MUTATIONS)
    with _real_sandboxed():
        try:
            yield
        finally:
            MUTATIONS.clear()
            MUTATIONS.update(mutations_before)


# ── A1-R1 — every real module loads under A1, in production's order ────────────


@pytest.mark.parametrize("module", _FEISHU_MODULES)
def test_a1_r1_real_feishu_module_loads_under_the_hook(module: str) -> None:
    """A1-R1: all 15 ``_feishu/*`` modules load through the hook, one by one.

    Parametrised per module rather than looped inside one test so a single
    regressing domain names itself, and so "15 modules covered" is visible in the
    run rather than a claim in a docstring.

    Order A (``_feishu_impl`` first) is production's order and the only one the
    repo's cycles allow; R2 measures what happens without it.  The scoped name
    and the file behind it are both checked: a module that loaded from some other
    layer's copy would still be importable, just wrong.
    """
    verdict = _verdict(_import_real_module_under_a1(module, impl_first=True))

    assert verdict["ok"], f"_feishu.{module} 在 A1 钩子下加载失败: {verdict.get('error')} {verdict.get('message')}"
    assert verdict["name"] == f"psi_layer_official._feishu.{module}", f"模块没进 A1 的作用域名: {verdict['name']}"
    assert Path(verdict["file"]) == _FEISHU_PKG / f"{module}.py", f"作用域名背后是另一份文件: {verdict['file']}"


@pytest.mark.parametrize("module", _FEISHU_MODULES)
def test_a1_r1_hook_does_not_claim_third_party_imports(module: str) -> None:
    """A1-R1b: ``psi_agent.*`` and third-party names stay on the normal machinery.

    The hook's dispatch rule is "first segment starts with ``_``".  Everything the
    real modules also import — ``lark_channel``, ``anyio``, ``loguru``, ``yaml``,
    ``psi_agent.session.*`` — must therefore be untouched, and must be *present*
    under its own name.  Checking only "no scoped junk appeared" would pass for a
    module whose third-party imports silently never happened.
    """
    verdict = _verdict(_import_real_module_under_a1(module, impl_first=True))

    assert verdict["ok"], f"_feishu.{module} 加载失败, 无法判断分流: {verdict.get('message')}"
    assert verdict["misclaimed"] == [], f"钩子误接管了非私有名字: {verdict['misclaimed']}"
    assert verdict["third_party_normal"], "没有任何第三方/一方模块以正常名字出现 —— 分流可能把它们整体吞了"


# ── A1-R2 — the cycle fix, re-measured under A1 ───────────────────────────────


@pytest.mark.parametrize("module", _FEISHU_MODULES)
def test_a1_r2_standalone_import_matches_the_known_cycle_list(module: str) -> None:
    """A1-R2: A1 neither fixes nor worsens the 12 known cycles.

    ``test_feishu_tool_import_cycles`` pins the same 12 modules as
    ``xfail(strict=True)`` under *bare-name* imports.  A1 renames modules, which
    is a different code path through ``sys.modules``, so the outcome has to be
    re-measured rather than inherited: a rename could plausibly have broken a
    cycle (two names where there was one) or created one.

    Written as an equality against ``_KNOWN_CYCLIC`` rather than as ``xfail`` so
    that *both* directions fail loudly — a cycle that A1 fixes is as much a result
    as one it breaks, and an ``xfail`` would have quietly swallowed the good news.
    """
    verdict = _verdict(_import_real_module_under_a1(module, impl_first=False))
    cyclic = not verdict["ok"] and "partially initialized module" in verdict.get("message", "")
    expected_cyclic = module in _KNOWN_CYCLIC

    assert verdict["ok"] or cyclic, (
        f"_feishu.{module} 独立导入失败, 但不是环路 —— A1 引入了新的失败模式: "
        f"{verdict.get('error')} {verdict.get('message')}"
    )
    assert cyclic == expected_cyclic, (
        f"_feishu.{module} 的环路状态在 A1 下变了 (A1 下{'成环' if cyclic else '不成环'}, "
        f"裸名基线{'成环' if expected_cyclic else '不成环'}) —— 若是变好, 说明 A1 顺手修了环路"
    )


def test_a1_r2_ledger_schema_dotted_import_still_resolves_once() -> None:
    """A1-R2b: the cycle fix's own import line survives the rename.

    ``_feishu_impl`` line 1147 is ``from _feishu.ledger_schema import (...)`` —
    the dotted form that needs all three of the probe's scaffolds at once.  The
    fix is built on that line working, so A1 has to keep it working, and keep it
    resolving to *one* object: ``feishu_mentor_ledger_cycle_table`` reads the
    schema through ``_core`` while ``mentor_ledger`` uses its own module-level
    name, and two lists would let a schema edit reach only one of the two tools.
    """
    proc = _run_child(
        """
        with a1._a1_layers_open([LAYER]) as hook:
            token = a1._asking_layer.set("official")
            try:
                core = __import__("_feishu_impl")
                hook.release_aliases()
                ml = __import__("_feishu.mentor_ledger", fromlist=["mentor_ledger"])
                same = core._LEDGER_SCHEMA_FIELDS is ml._LEDGER_SCHEMA_FIELDS
                print("VERDICT " + json.dumps({"ok": True, "same_object": same,
                                               "prefix": core._LEDGER_NAME_PREFIX}))
            except Exception as exc:
                print("VERDICT " + json.dumps({"ok": False, "error": type(exc).__name__,
                                               "message": str(exc)[:200]}))
            finally:
                a1._asking_layer.reset(token)
                hook.release_aliases()
        """
    )
    verdict = _verdict(proc)

    assert verdict["ok"], f"A1 下 ledger_schema 的 dotted import 链断了: {verdict.get('message')}"
    assert verdict["same_object"], "两条导入路径拿到了两份 _LEDGER_SCHEMA_FIELDS —— 常量下沉在 A1 下漂移了"
    assert verdict["prefix"] == "TODO 台账-", f"下沉的常量值不对: {verdict['prefix']!r}"


# ── A1-R6 — the ToC side, observed only ───────────────────────────────────────


@pytest.mark.parametrize("helper", _desktop_private_helpers())
def test_a1_r6_desktop_private_helper_loads_under_the_hook(helper: str) -> None:
    """A1-R6: ``agents/desktop/tools/_*.py`` also load under the hook.

    ``desktop`` is ToC code this card may only read, so this observes rather than
    constrains: it loads each helper through A1 and reports.  It is a second,
    independent population (29 flat bare-name modules, where ``_feishu`` is one
    dotted package), which is what makes it worth measuring — the bare-name shape
    needs none of the dotted scaffolding, so a failure here would mean something
    much more basic than a cycle.

    Parametrised over what is on disk, so a helper added to ToC is covered without
    editing this file, and no count is pinned that an unrelated ToC change could
    break.
    """
    verdict = _verdict(
        _run_child(
            f"""
            LAYER = a1.make_layer("official", (ROOT / "agents/desktop/tools").resolve())
            with a1._a1_layers_open([LAYER]) as hook:
                token = a1._asking_layer.set("official")
                try:
                    __import__({helper!r})
                    scoped = sys.modules["psi_layer_official." + {helper!r}]
                    print("VERDICT " + json.dumps({{"ok": True, "name": scoped.__name__}}))
                except Exception as exc:
                    print("VERDICT " + json.dumps({{
                        "ok": False, "error": type(exc).__name__, "message": str(exc)[:200],
                    }}))
                finally:
                    a1._asking_layer.reset(token)
                    hook.release_aliases()
            """
        )
    )

    assert verdict["ok"], f"desktop 的 {helper} 在 A1 钩子下加载失败: {verdict.get('error')} {verdict.get('message')}"
    assert verdict["name"] == f"psi_layer_official.{helper}", f"没进作用域名: {verdict['name']}"


# ── A1-R3 — the same real package, opened as two layers at once ────────────────


def _real_feishu_copy_layer(root: Path, layer_id: str, marker: str) -> Layer:
    """Copy the real ``_feishu`` package into a ``tmp_path`` layer, tagged *marker*.

    Two layers need two *distinguishable* copies, and the repo ships one, so the
    copies are made under ``tmp_path`` and the tag is appended to
    ``ledger_schema``'s ``_LEDGER_NAME_PREFIX`` — an existing module-level
    constant, so the file stays syntactically what it was and no import is added.
    ``_feishu_impl`` and the other top-level helpers come along because the
    package's members import them by bare name.

    This is real code (15 real modules, real third-party imports) in a synthetic
    *arrangement*.  The originals under ``agents/`` are read, never written.
    """
    tools_dir = root / layer_id / "tools"
    tools_dir.mkdir(parents=True)
    shutil.copytree(_FEISHU_PKG, tools_dir / "_feishu")
    for helper in _FEISHU_TOOLS.glob("_*.py"):
        shutil.copy2(helper, tools_dir / helper.name)

    schema = tools_dir / "_feishu" / "ledger_schema.py"
    original = schema.read_text(encoding="utf-8")
    tagged = original.replace('_LEDGER_NAME_PREFIX = "TODO 台账-"', f'_LEDGER_NAME_PREFIX = "{marker}"')
    assert tagged != original, "ledger_schema 里的 _LEDGER_NAME_PREFIX 变了 —— R3 的标记没打上, 判据会假绿"
    schema.write_text(tagged, encoding="utf-8")

    # A public tool file, the way the registry loads one: bare import of the
    # layer's own impl, then the dotted read that carries the marker out.
    (tools_dir / "probe.py").write_text(
        textwrap.dedent(
            """
            import _feishu_impl as _core
            from _feishu.ledger_schema import _LEDGER_NAME_PREFIX

            SEEN = _LEDGER_NAME_PREFIX
            SEEN_VIA_CORE = _core._LEDGER_NAME_PREFIX
            """
        ),
        encoding="utf-8",
    )
    return make_layer(layer_id, tools_dir)


def test_a1_r3_two_copies_of_the_real_package_stay_apart(tmp_path: Path) -> None:
    """A1-R3: with the real ``_feishu`` open as two layers, each tool reads its own.

    The case the kernel cannot do today: one ``sys.modules['_feishu']`` slot for
    both layers.  Under A1 they are ``psi_layer_enterprise._feishu`` and
    ``psi_layer_personal._feishu``.  Read back through two routes — the tool's own
    dotted import and ``_feishu_impl``'s re-export — because the re-export path is
    the one 59 tool files actually use, and it could bind the other layer's copy
    while the direct import looked right.
    """
    enterprise = _real_feishu_copy_layer(tmp_path, "enterprise", "ENTERPRISE 台账-")
    personal = _real_feishu_copy_layer(tmp_path, "personal", "PERSONAL 台账-")

    modules = _exec_layers_together([enterprise, personal])

    assert modules["enterprise/probe"].SEEN == "ENTERPRISE 台账-", "enterprise 层读到了别层的 _feishu"
    assert modules["personal/probe"].SEEN == "PERSONAL 台账-", "personal 层读到了别层的 _feishu"
    assert modules["enterprise/probe"].SEEN_VIA_CORE == "ENTERPRISE 台账-", (
        "enterprise 的 _feishu_impl 再导出串到了别层"
    )
    assert modules["personal/probe"].SEEN_VIA_CORE == "PERSONAL 台账-", "personal 的 _feishu_impl 再导出串到了别层"


# ── A1-R4 / R5 / R7 — three layers ────────────────────────────────────────────

# Most-specific-first, derived from the probe's ``LAYER_PRIORITY`` rather than
# written out again: the ladder is one fact and duplicating it here would let the
# fixtures and the thing under test disagree without anything going red.
#
# ``_search_order`` puts the asking layer first and then "the rest" *by
# priority*, so with three layers the rest needs an order of its own — which two
# layers can never show, because "the rest" is a set of one.
_PRECEDENCE = tuple(sorted(LAYER_PRIORITY, key=lambda lid: LAYER_PRIORITY[lid], reverse=True))

# Every rotation of which layer gets loaded first, since load order is glob order
# in production and not something a layer controls.
_THREE_ORDERS = pytest.mark.parametrize(
    "first",
    ["personal", "enterprise", "official"],
)


def _ordered_three(layers: dict[str, Layer], first: str) -> list[Layer]:
    """The three layers with *first* moved to the front, precedence otherwise kept."""
    rest = [lid for lid in _PRECEDENCE if lid != first]
    return [layers[first]] + [layers[lid] for lid in rest]


def _three_layers(root: Path, *, provide: dict[str, Sequence[str]]) -> dict[str, Layer]:
    """Build three layers; *provide* says which of them ship ``_shared``.

    Each layer gets a ``probe.py`` importing ``_shared`` by bare name, so the
    verdict is always "which layer's file answered", never an inspection of
    ``sys.modules``.
    """
    layers: dict[str, Layer] = {}
    for layer_id in _PRECEDENCE:
        tools_dir = root / layer_id / "tools"
        tools_dir.mkdir(parents=True)
        if "_shared" in provide.get(layer_id, ()):
            (tools_dir / "_shared.py").write_text(f"MARKER = {layer_id!r}\n", encoding="utf-8")
        # ``via_name`` carries the resolved module's own ``__name__`` out with the
        # marker.  Without it these criteria cannot tell the hook's answer from
        # plain ``sys.path``'s: the layer dirs are on ``sys.path`` (the kernel
        # puts them there), so when the hook declines to resolve, normal import
        # finds the same file and the marker matches anyway.  Measured — R4
        # stayed green under ``no_cross_layer_fallback`` until ``via_name`` was added.
        (tools_dir / "probe.py").write_text(
            textwrap.dedent(
                """
                import _shared

                SEEN = _shared.MARKER
                via_name = _shared.__name__
                """
            ),
            encoding="utf-8",
        )
        layers[layer_id] = make_layer(layer_id, tools_dir)
    return layers


@_THREE_ORDERS
def test_a1_r4_personal_falls_back_to_official_when_only_official_has_it(tmp_path: Path, first: str) -> None:
    """A1-R4: a name only the official layer ships is reachable from personal.

    The three-layer version of P3: the bottom layer must still catch what the two
    layers above it do not define, whichever layer was loaded first.

    ``via_name`` is asserted alongside the marker so that the *hook* is what resolved
    the name.  The marker alone does not establish that: the layer dirs sit on
    ``sys.path``, so a hook that declines every fallback still yields the same
    marker via ordinary import.
    """
    layers = _three_layers(tmp_path, provide={"official": ["_shared"]})

    modules = _exec_layers_together(_ordered_three(layers, first))

    resolved_by_hook = _scoped_name("official", "_shared")
    for asking in ("personal", "enterprise"):
        probe = modules[f"{asking}/probe"]
        assert probe.SEEN == "official", f"{asking} 层没能回退到官方层"
        assert probe.via_name == resolved_by_hook, (
            f"{asking} 层拿到的 _shared 不是钩子解析的 (实际 {probe.via_name!r}) —— 判据落在 sys.path 上而非 A1 上"
        )


@_THREE_ORDERS
def test_a1_r5_middle_layer_wins_over_official(tmp_path: Path, first: str) -> None:
    """A1-R5: when enterprise *and* official both ship a name, personal gets enterprise.

    The criterion two layers structurally cannot express, and the one that catches
    a broken middle layer: R4 alone stays green when fallback skips enterprise
    entirely, because official is there to catch everything.

    Measured before it was written, and it failed: ``_search_order`` used to return
    "the asking layer, then the remaining layers **in the order the hook was
    constructed with**", i.e. in *open* order.  So it passed for ``[personal,
    enterprise, official]`` and failed for ``[official, enterprise, personal]``
    with ``'official' != 'enterprise'`` — the fourth piece of scaffolding, and one
    the two-layer probe could not have found because there "the rest" is a set of
    one.  Fixed by ``_ranked_layers`` sorting on ``Layer.priority``; the parameter
    that used to be ``xfail(strict=True)`` is now plain.

    Parametrised over all three open orders on purpose: a single order cannot tell
    priority-sorting from a list that happened to arrive sorted.
    """
    layers = _three_layers(tmp_path, provide={"official": ["_shared"], "enterprise": ["_shared"]})

    modules = _exec_layers_together(_ordered_three(layers, first))

    assert modules["personal/probe"].SEEN == "enterprise", (
        "个人层缺的名字穿到了官方层, 没在企业层命中 —— 中间层坏了而 R4 照样绿"
    )
    resolved_from_enterprise = _scoped_name("enterprise", "_shared")
    assert modules["personal/probe"].via_name == resolved_from_enterprise, (
        f"个人层拿到的 _shared 不是钩子从企业层解析的: {modules['personal/probe'].via_name!r}"
    )
    assert modules["enterprise/probe"].SEEN == "enterprise", "企业层自己有的名字反而去了官方层"


@_THREE_ORDERS
def test_a1_r7_asking_layer_beats_a_higher_priority_layer(tmp_path: Path, first: str) -> None:
    """A1-R7: 企业层自己有 ``_shared`` 时, 优先级更高的个人层不得抢答。

    R5 定了"回退按优先级", 这条定的是**回退之前那一步**: 提问层自己有的名字, 归提问层。
    两条规则在 R5 的形状里无法区分 —— 那里个人层根本没有 ``_shared``, "自己优先"没东西
    可优先。这条判据把提问层放在中间一档 (企业), 让它自己和更高优先级的个人层都有这个
    名字, 于是两种语义给出不同答案:

    * 提问层优先 → 企业层拿自己那份 (本判据断言的)
    * 优先级优先 → 企业层拿到个人层那份

    选前者, 依据是**它不是一个新决定, 而是 A1 已有判据的同一件事**: P1/P2/R3 都是
    "两层都有同名 helper, 各自必须绑自己那份", 只不过那里提问层恰好就是优先级最高的
    层, 所以看不出这是"自己优先"还是"优先级优先"在起作用。这条判据把提问层挪到中间一档,
    才让两者分开。

    等价性是实测出来的, 不是推的: 把 ``priority_over_asking_layer`` 打开(纯按优先级
    排序、不给提问层特权), 转红的是 P1/P2/P3/P6 与 R3/R7 —— 其中 R3 用的是真实
    ``_feishu`` 的两份拷贝。也就是说"优先级优先"不是一个可选语义, 它会直接拆掉 A1 的
    隔离本身, 连真实模块那条都保不住。反过来, 与优先级有关的另外两个 knob
    (``ignore_layer_priority`` / ``reverse_layer_priority``) 都**不**动这条判据 ——
    提问层在别层的排序被考虑之前就已经答完了。两张 ``EXPECTED_BREAKAGE`` 里都记着。

    语义上的道理是同一件事的另一面: ``_`` 开头的私有 helper 是某层的**实现**, 不是它
    对外的可寻址接口(``tools`` 属性的 last-wins 才是那个接口)。个人层新写一个碰巧叫
    ``_shared`` 的私有文件, 不该改变企业层某个工具的行为 —— 那正是 A1 要消除的碰撞。

    ``via_name`` 与 R4/R5 同理必须断言, 而且实测证明它在这里是承重的, 不是照抄的样板:
    把 ``find_spec`` 改成一律 ``return None``(钩子完全不接管), 三个参数里有**两个**
    (``first=personal`` 和 ``first=official``) 只有 ``via_name`` 这条断言转红 ——
    marker 照旧读到 ``'enterprise'``, 因为层目录在 ``sys.path`` 上、企业层的
    ``_shared.py`` 就在那儿, 普通 import 给出同样答案。只比 marker 的话这条判据会
    三分之二假绿, 正是 R4 踩过的那个坑。
    """
    layers = _three_layers(tmp_path, provide={"enterprise": ["_shared"], "personal": ["_shared"]})

    modules = _exec_layers_together(_ordered_three(layers, first))

    probe = modules["enterprise/probe"]
    assert probe.SEEN == "enterprise", (
        "企业层自己有 _shared 却拿到了优先级更高的个人层那份 —— 私有 helper 被下游层顶掉了"
    )
    assert probe.via_name == _scoped_name("enterprise", "_shared"), (
        f"企业层拿到的 _shared 不是钩子从企业层解析的 (实际 {probe.via_name!r}) —— 判据落在 sys.path 上而非 A1 上"
    )
    assert modules["personal/probe"].SEEN == "personal", "个人层自己有的名字反而去了别层"


# ── mutation red-check for the new criteria ────────────────────────────────────
#
# The sibling probe's ``EXPECTED_BREAKAGE`` covers P1-P5 on synthetic layers.  The
# criteria added here need their own mapping, and it has to be an *in-process* one:
# R1/R2 run the hook in child interpreters, where a knob flipped in this process
# has no effect.  So the knob-driven checks below are the ones whose subject is
# multi-layer resolution (R3/R4/R5), and R1/R2's red-check is recorded in the
# commit message as a manual measurement instead of pretended here.
#
# ``drop_layer_prefix`` is the knob that matters for R3: it collapses two layers'
# scoped names into one shared key, which is the collision A1 exists to remove.


def _r3_holds(tmp_path: Path) -> bool:
    enterprise = _real_feishu_copy_layer(tmp_path, "enterprise", "ENTERPRISE 台账-")
    personal = _real_feishu_copy_layer(tmp_path, "personal", "PERSONAL 台账-")
    try:
        modules = _exec_layers_together([enterprise, personal])
    except Exception:
        return False
    return (
        modules["enterprise/probe"].SEEN == "ENTERPRISE 台账-"
        and modules["personal/probe"].SEEN == "PERSONAL 台账-"
        and modules["enterprise/probe"].SEEN_VIA_CORE == "ENTERPRISE 台账-"
    )


def _r4_holds(tmp_path: Path) -> bool:
    layers = _three_layers(tmp_path, provide={"official": ["_shared"]})
    try:
        modules = _exec_layers_together(_ordered_three(layers, "personal"))
    except Exception:
        return False
    resolved_by_hook = _scoped_name("official", "_shared")
    return all(
        modules[f"{asking}/probe"].SEEN == "official" and modules[f"{asking}/probe"].via_name == resolved_by_hook
        for asking in ("personal", "enterprise")
    )


def _r5_holds(tmp_path: Path) -> bool:
    # ``official`` first on purpose: this is the open order the priority bug used to
    # fail on, so a knob that reverts to open order has to be visible here.  With
    # ``personal`` first the list arrives already sorted and the knob looks harmless.
    layers = _three_layers(tmp_path, provide={"official": ["_shared"], "enterprise": ["_shared"]})
    try:
        modules = _exec_layers_together(_ordered_three(layers, "official"))
    except Exception:
        return False
    resolved_from_enterprise = _scoped_name("enterprise", "_shared")
    return (
        modules["personal/probe"].SEEN == "enterprise"
        and modules["personal/probe"].via_name == resolved_from_enterprise
        and modules["enterprise/probe"].SEEN == "enterprise"
    )


def _r7_holds(tmp_path: Path) -> bool:
    layers = _three_layers(tmp_path, provide={"enterprise": ["_shared"], "personal": ["_shared"]})
    try:
        modules = _exec_layers_together(_ordered_three(layers, "personal"))
    except Exception:
        return False
    return (
        modules["enterprise/probe"].SEEN == "enterprise"
        and modules["enterprise/probe"].via_name == _scoped_name("enterprise", "_shared")
        and modules["personal/probe"].SEEN == "personal"
    )


REAL_CRITERIA = {"R3": _r3_holds, "R4": _r4_holds, "R5": _r5_holds, "R7": _r7_holds}

# knob → the criteria it must redden; anything unlisted must stay green.  Measured
# by running it, not predicted — two entries here are *not* what was first written
# down, and both corrections were the point of doing it:
#
# * ``no_cross_layer_fallback`` initially reddened only R5.  R4 was passing for a
#   reason unrelated to the hook: the layer dirs are on ``sys.path``, so when the
#   hook declines the fallback, ordinary import finds the same file and the marker
#   still matched.  R4/R5 now also assert ``via_name`` (the resolved module's
#   ``__name__``), which is what makes them witness A1 rather than ``sys.path``.
# * ``ignore_asking_layer`` does not redden R5, and R5 was changed *not* to claim
#   it does.  In R5's shape the asking layer (personal) has no ``_shared`` at all,
#   so "prefer the asking layer" has nothing to prefer and ignoring it lands on
#   enterprise either way.  That is a coincidence of the shape, not the knob being
#   harmless — R3 and R7, where the asking layer does ship the module, are its
#   witnesses.
#
# ``no_module_reuse`` and ``no_scope_package`` are listed nowhere here because they
# redden nothing in this file: measured, both leave R3/R4/R5/R7 green.  Their
# witnesses are P5 and P6 in the sibling probe.  Absent rather than empty-set
# because ``test_real_mutations_*`` only iterates this table's keys, so an entry
# would assert nothing new; the probe's table lists its empty rows because there the
# iteration covers every knob.
#
# R1/R2 cannot be driven from this table — they run the hook in child interpreters,
# where a knob flipped in this process has no effect — so their red-check was done
# by editing the prototype's source and re-running.  Measured, for the record:
#
#   scaffold 1 off (``bind_alias`` a no-op) → 46/46 R1+R2 red, R3 red, R4/R5 green
#   ``drop_layer_prefix``                   → 33/46 R1+R2 red
#   ``leak_plain_aliases``                  → 46/46 R1+R2 **green**
#
# The last line is a real limitation, not a passing note: a leaked plain alias is
# invisible to R1/R2 because each runs in a fresh interpreter that exits before the
# leak could affect anything.  Residue is P4's subject and stays there.  That
# scaffold 1 reddens R1/R2/R3 but not R4/R5 is what shows the criteria are not
# shadowing each other file-wide: the dotted shapes need it, the bare-name ones
# do not.
#
# The four priority-related rows below are measured, and two of them corrected a
# prediction:
#
# * ``ignore_layer_priority`` and ``reverse_layer_priority`` redden **R5 only** —
#   not R4, and not R7.  R4 was predicted to move with them and does not: only
#   official ships ``_shared`` there, so every search order reaches the same single
#   file.  R7 does not move either, because the asking layer answers before the
#   ranking of the others is consulted at all.  R5 is the sole witness for both
#   knobs, which is exactly why the fourth scaffolding was invisible until R5
#   existed.
# * ``drop_layer_prefix`` and ``leak_plain_aliases`` now also redden R5 and R7,
#   where before they reddened R3 alone.  Not a regression: both knobs collapse two
#   layers onto one ``sys.modules`` key, and R5/R7 are new criteria that put two
#   layers' ``_shared`` in play, so they are now among the things that collapse can
#   be seen through.
# * ``priority_over_asking_layer`` reddens R3 and R7.  R3 moving is the substance of
#   R7's decision: "highest priority answers, even for the asking layer" breaks the
#   real ``_feishu`` two-copy isolation, not just the synthetic three-layer shape.
#
# No knob reddens all of R3/R4/R5/R7, and R5 and R7 each have a knob no other
# criterion witnesses, so a regression in either is localisable.
REAL_EXPECTED_BREAKAGE = {
    "drop_layer_prefix": {"R3", "R5", "R7"},
    "ignore_asking_layer": {"R3", "R7"},
    "no_cross_layer_fallback": {"R4", "R5"},
    "leak_plain_aliases": {"R3", "R5", "R7"},
    "ignore_layer_priority": {"R5"},
    "reverse_layer_priority": {"R5"},
    "priority_over_asking_layer": {"R3", "R7"},
}


@pytest.mark.parametrize("knob", sorted(REAL_EXPECTED_BREAKAGE))
def test_real_mutations_redden_exactly_their_own_criteria(tmp_path: Path, knob: str) -> None:
    """Each knob must break the criteria it owns here, and only those.

    Same discipline as the sibling probe: a knob that reddens everything would mean
    R3/R4/R5 shadow each other and none of them could localise a regression.
    """
    expected = REAL_EXPECTED_BREAKAGE[knob]
    broken = set()
    with mutated(knob):
        for name, check in REAL_CRITERIA.items():
            with _real_sandboxed():
                if not check(tmp_path / f"{knob}-{name}"):
                    broken.add(name)

    assert broken == expected, f"mutation {knob!r} broke {sorted(broken)}, expected exactly {sorted(expected)}"
