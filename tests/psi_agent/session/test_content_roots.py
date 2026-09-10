"""Criteria for content roots — the layer identity that makes reuse reachable.

The payoff this file exists to prove is C1: **one content root mounted into two
workspaces compiles once.** It is measured by counting ``compile`` calls, not by
reading ``_module_cache`` or ``sys.modules``. Counting the work is what the
benefit actually is; asserting on the cache dict would pass just as happily if
the cache were populated and then ignored.

The other half is that reuse must not have been bought by weakening isolation.
The I-series holds the line the old path-derived id held: two *differently named*
roots with a byte-identical file still compile twice and still bind their own
private helper. C1 and I1 are one pair — an implementation that keys on the hash
alone passes C1 and fails I1, which is exactly the silent mistake this card
warns about.

``test_mutations_redden_exactly_their_own_criteria`` is the red-check: each
mutation replaces one shipping behaviour and the map from mutation to reddened
criteria is an executable assertion, so a criterion that stops being load-bearing
is caught rather than passing quietly.
"""

from __future__ import annotations

import builtins
import inspect
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session import content_roots as cr
from psi_agent.session import tool_registry
from psi_agent.session.content_roots import CONTENT_ROOTS_ENV, ContentRoot
from psi_agent.session.tool_registry import ToolRegistry, _module_cache


# Reached through their modules rather than imported by name, deliberately: the
# red-check replaces these as module attributes, and a name bound at import time
# would keep pointing at the original — the criterion would then pass under its
# own mutation and the red-check could never fail. ``ContentRoot`` is imported
# directly because a class object is patched in place.
def _parse(raw: str) -> list[ContentRoot]:
    """``parse_content_roots`` resolved at call time."""
    return cr.parse_content_roots(raw)


def _from_env(env: dict[str, str] | None = None) -> list[ContentRoot]:
    """``content_roots_from_env`` resolved at call time."""
    return cr.content_roots_from_env(env)


def _layer_id(tools_dir: Path, roots: Sequence[ContentRoot] | None = None) -> str:
    """``tool_registry._layer_id`` resolved at call time."""
    return tool_registry._layer_id(tools_dir, roots)


# ── the shipped tool file every criterion mounts ──────────────────────────────

# Imports a same-directory private helper and reads a marker out of it, so a
# module handed over from the wrong layer is visible as the wrong marker rather
# than as an import error. Same shape as the A1 fixtures.
TOOL_SRC = """
import _root_helper


async def probe_tool() -> str:
    \"\"\"Report which layer's helper answered.\"\"\"
    return _root_helper.MARKER


WHICH = _root_helper.MARKER
"""


def _write_root(path: Path, marker: str, *, tool_src: str = TOOL_SRC) -> Path:
    """Lay out ``{path}/tools`` with one tool file and one private helper."""
    tools = path / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "_root_helper.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (tools / "probe.py").write_text(tool_src, encoding="utf-8")
    return tools


async def _call(registry: ToolRegistry, name: str) -> object:
    """Call tool *name*, asserting first that it is reachable at all.

    ``get()`` is typed ``| None`` because a miss is a real answer, so the
    reachability half is asserted here rather than left to a ``None`` sneaking
    into a call and surfacing as a confusing ``TypeError``.
    """
    func = registry.get(name)
    assert func is not None, f"get({name!r}) returned None"
    return await func()


@contextmanager
def _clean_cache() -> Iterator[None]:
    """Run with an empty process cache, and leave it as it was found.

    Every compile-count criterion depends on starting cold: a warm entry from
    another test would make a first load look like a reuse, and the count would
    read as a pass for the wrong reason.
    """
    saved = dict(_module_cache)
    _module_cache.clear()
    try:
        yield
    finally:
        _module_cache.clear()
        _module_cache.update(saved)


@contextmanager
def _declared(roots: Sequence[ContentRoot]) -> Iterator[None]:
    """Declare *roots* in the environment the way a deployment does."""
    entries = os.pathsep.join(f"{root.name}={root.path}" for root in roots)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(CONTENT_ROOTS_ENV, entries)
    try:
        yield
    finally:
        monkeypatch.undo()


class _CompileCounter:
    """Counts ``compile`` calls on the tool sources, ignoring everything else.

    Patching ``builtins.compile`` catches the registry's call without the
    registry having to expose a counter, so the criterion measures the shipping
    path rather than a test-only hook. Filtering by filename keeps unrelated
    compiles (pytest's own assertion rewriting, ``re``) out of the number.
    """

    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.filenames: list[str] = []

    @property
    def count(self) -> int:
        return len(self.filenames)


@contextmanager
def _counting_compiles(needle: str = "probe.py") -> Iterator[_CompileCounter]:
    counter = _CompileCounter(needle)
    real_compile = builtins.compile

    def counting(source, filename, mode, *args, **kwargs):  # type: ignore[no-untyped-def]
        if needle in str(filename):
            counter.filenames.append(str(filename))
        return real_compile(source, filename, mode, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(builtins, "compile", counting)
    try:
        yield counter
    finally:
        monkeypatch.undo()


# ── C-series: the reuse this card is for ──────────────────────────────────────


@pytest.mark.anyio
async def test_c1_one_content_root_two_workspaces_compiles_once(tmp_path: Path) -> None:
    """C1 — the payoff. One declared root, two mounts, one ``compile``.

    Two workspaces mount the same official root (the production shape: a
    read-only bind mount into many containers is one body of bytes reached by
    two paths). The second load must do no compiling: same declared root, same
    bytes, so the already-exec'd module is handed back.
    """
    official = tmp_path / "official"
    _write_root(official, "OFFICIAL")
    root = ContentRoot(name="official", path=official, priority=0)

    with _clean_cache(), _declared([root]):
        with _counting_compiles() as first:
            reg_a = await ToolRegistry.load_content_roots([root], session_id="ws-a")
        with _counting_compiles() as second:
            reg_b = await ToolRegistry.load_content_roots([root], session_id="ws-b")

    assert first.count == 1, "first load must compile the tool file"
    assert second.count == 0, f"second workspace re-compiled {second.filenames}"
    # Reuse must still produce a working registry, not just a cheap one.
    assert await _call(reg_a, "probe_tool") == "OFFICIAL"
    assert await _call(reg_b, "probe_tool") == "OFFICIAL"


@pytest.mark.anyio
async def test_c2_same_content_at_two_paths_is_one_layer(tmp_path: Path) -> None:
    """C2 — identity is the declared name, not the path.

    The same content copied to a second location and declared under the same
    name compiles once. This is the property a path-derived id cannot have, and
    it is what separates "content root" from "tools dir".
    """
    first_mount = tmp_path / "mount-a" / "official"
    second_mount = tmp_path / "mount-b" / "official"
    _write_root(first_mount, "OFFICIAL")
    _write_root(second_mount, "OFFICIAL")

    root_a = ContentRoot(name="official", path=first_mount, priority=0)
    root_b = ContentRoot(name="official", path=second_mount, priority=0)

    with _clean_cache(), _declared([root_a]):
        with _counting_compiles() as first:
            await ToolRegistry.load_content_roots([root_a], session_id="ws-a")
        with _declared([root_b]), _counting_compiles() as second:
            await ToolRegistry.load_content_roots([root_b], session_id="ws-b")

    assert first.count == 1
    assert second.count == 0, f"same name at a different path re-compiled {second.filenames}"


@pytest.mark.anyio
async def test_c3_layer_id_is_the_declared_name_not_a_path(tmp_path: Path) -> None:
    """C3 — ``_layer_id`` returns the declared name for a claimed dir.

    The unit behind C1/C2. Asserting the name is *absent* from the id would pass
    for an empty string, so this asserts the value and, separately, that no path
    separator leaked in.
    """
    official = tmp_path / "official"
    tools = _write_root(official, "OFFICIAL")
    root = ContentRoot(name="official", path=official, priority=0)

    assert _layer_id(tools, [root]) == "official"
    assert os.sep not in _layer_id(tools, [root])


def test_c5_layer_id_consults_the_declared_environment(tmp_path: Path) -> None:
    """C5 — the id is found from the deployment's declaration, not just arguments.

    ``_layer_id`` is called from the single-dir load with no roots passed, so it
    has to read the declaration itself. Without this the env plumbing could be
    dead code and every other criterion would still pass by handing roots in
    explicitly.
    """
    official = tmp_path / "official"
    tools = _write_root(official, "OFFICIAL")
    root = ContentRoot(name="official", path=official, priority=0)

    with _declared([root]):
        assert _layer_id(tools) == "official"


@pytest.mark.anyio
async def test_c4_undeclared_dir_falls_back_to_its_own_path(tmp_path: Path) -> None:
    """C4 — with nothing declared the id is the dir path, as before.

    The single-root world has to be untouched: one dir is one layer and the path
    is the only identity available. A shared constant here would make every
    unclaimed dir one layer and hand byte-identical files each other's modules.
    """
    tools = _write_root(tmp_path / "lonely", "LONELY")

    assert _layer_id(tools, []) == str(tools.resolve())

    other = _write_root(tmp_path / "other", "OTHER")
    assert _layer_id(tools, []) != _layer_id(other, [])


# ── I-series: isolation must not have been traded away for the reuse ──────────


@pytest.mark.anyio
async def test_i1_two_named_roots_with_identical_bytes_compile_twice(tmp_path: Path) -> None:
    """I1 — the guard on C1. Different layers never share a module.

    Two roots ship a byte-identical ``probe.py`` and a *different*
    ``_root_helper.py``. Keyed correctly they compile twice and each sees its
    own helper; keyed on the hash alone they would compile once and the second
    root would read the first root's marker. That is the failure mode this card
    calls the easiest thing to get silently wrong.
    """
    official = tmp_path / "official"
    personal = tmp_path / "personal"
    _write_root(official, "OFFICIAL")
    _write_root(personal, "PERSONAL")
    # Same tool bytes in both roots — the precondition that makes this a test of
    # the key rather than of the hash.
    assert (official / "tools" / "probe.py").read_bytes() == (personal / "tools" / "probe.py").read_bytes()

    roots = [
        ContentRoot(name="official", path=official, priority=0),
        ContentRoot(name="personal", path=personal, priority=10),
    ]

    with _clean_cache(), _declared(roots), _counting_compiles() as counter:
        registry = await ToolRegistry.load_content_roots(roots, session_id="ws")

    assert counter.count == 2, f"identical bytes in two layers compiled {counter.count} time(s)"
    # Highest priority wins the public name, and it must be running its *own*
    # helper rather than the official root's.
    assert await _call(registry, "probe_tool") == "PERSONAL"


@pytest.mark.anyio
async def test_i2_distinct_names_keep_distinct_layer_ids(tmp_path: Path) -> None:
    """I2 — two declared roots never collapse onto one id."""
    official = tmp_path / "official"
    personal = tmp_path / "personal"
    official_tools = _write_root(official, "OFFICIAL")
    personal_tools = _write_root(personal, "PERSONAL")
    roots = [
        ContentRoot(name="official", path=official, priority=0),
        ContentRoot(name="personal", path=personal, priority=10),
    ]

    assert _layer_id(official_tools, roots) != _layer_id(personal_tools, roots)


@pytest.mark.anyio
async def test_i3_edited_file_recompiles_within_one_root(tmp_path: Path) -> None:
    """I3 — reuse is per bytes, so an edit still compiles.

    Guards the other direction from C1: an id that ignored the hash would serve
    a stale module forever, and the symptom would be a user's tool edit never
    taking effect.
    """
    official = tmp_path / "official"
    tools = _write_root(official, "OFFICIAL")
    root = ContentRoot(name="official", path=official, priority=0)

    with _clean_cache(), _declared([root]):
        with _counting_compiles() as first:
            await ToolRegistry.load_content_roots([root], session_id="ws-a")
        (tools / "probe.py").write_text(TOOL_SRC + "\nEDITED = True\n", encoding="utf-8")
        with _counting_compiles() as second:
            await ToolRegistry.load_content_roots([root], session_id="ws-b")

    assert first.count == 1
    assert second.count == 1, "changed bytes must be re-compiled"


# ── P-series: where the configuration comes from ──────────────────────────────


def test_p1_entries_parse_in_ascending_priority() -> None:
    """P1 — position sets rank, last entry most specific.

    The declaration reads in mount order (``official`` then ``users``), and the
    user-closest root must come out ranked highest — otherwise official tools
    would override personal ones.
    """
    raw = os.pathsep.join(["official=/official", "enterprise=/enterprise", "users=/users/alice"])
    roots = _parse(raw)

    assert [root.name for root in roots] == ["official", "enterprise", "users"]
    assert [root.priority for root in roots] == [0, 10, 20]
    assert roots[-1].priority == max(root.priority for root in roots)


def test_p2_windows_drive_letters_survive_parsing() -> None:
    """P2 — a path's colon is not a separator.

    ``os.pathsep`` rather than ``:`` is what makes ``C:/pack`` parseable at all;
    splitting on the colon would truncate every Windows path to ``C``.
    """
    roots = _parse(os.pathsep.join(["official=C:/packs/official", "users=C:/users/alice"]))

    assert [str(root.path) for root in roots] == [str(Path("C:/packs/official")), str(Path("C:/users/alice"))]


def test_p3_unnamed_and_malformed_entries_are_dropped_not_guessed() -> None:
    """P3 — no name, no root; and one bad entry does not sink the others.

    A name is the isolation boundary, so an entry without one is refused rather
    than given a derived default. Dropping instead of raising is deliberate: a
    typo in a deployment-wide env var must not make every Session unstartable.
    """
    raw = os.pathsep.join(["/official/with/no/name", "official=/official", "=/nameless", "bad name=/x"])
    roots = _parse(raw)

    assert [root.name for root in roots] == ["official"]


def test_p4_duplicate_names_in_one_list_are_refused() -> None:
    """P4 — one list cannot declare two mounts of one name.

    Sharing a name *across* deployments is the intended reuse (C2); two entries
    in one list would instead shadow each other's tools dir under a shared id.
    """
    roots = _parse(os.pathsep.join(["official=/a", "official=/b"]))

    assert [str(root.path) for root in roots] == [str(Path("/a"))]


def test_p5_unset_env_means_no_layering() -> None:
    """P5 — the feature is off until declared, and off means empty.

    A synthesised default root would put every existing deployment onto the new
    path without anyone asking for it.
    """
    assert _from_env({}) == []
    assert _from_env({CONTENT_ROOTS_ENV: "   "}) == []


def test_p6_root_derives_tools_dir_and_layer_id() -> None:
    """P6 — the ``ContentRoot`` → ``Layer`` handoff keeps name and rank."""
    root = ContentRoot(name="official", path=Path("/official"), priority=30)
    layer = root.as_layer()

    assert layer.layer_id == "official"
    assert layer.tools_dir == Path("/official") / "tools"
    assert layer.priority == 30


# ── R-series: the red lines ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_r1_get_reaches_every_registered_tool_across_roots(tmp_path: Path) -> None:
    """R1 — ``get()`` stays full-set reachable; layering never hides a tool.

    Layering applies at table-building time only. A tool that a higher layer
    does not override must still be callable, and ``get()`` must never answer
    ``None`` for a registered name. 看不见 ≠ 调不到.
    """
    official = tmp_path / "official"
    personal = tmp_path / "personal"
    official_tools = _write_root(official, "OFFICIAL")
    _write_root(personal, "PERSONAL")
    # A tool only the official root ships — nothing above it to override it.
    (official_tools / "only_official.py").write_text(
        "async def official_only_tool() -> str:\n"
        '    """A tool no other layer ships."""\n'
        "    return 'ONLY-OFFICIAL'\n",
        encoding="utf-8",
    )

    roots = [
        ContentRoot(name="official", path=official, priority=0),
        ContentRoot(name="personal", path=personal, priority=10),
    ]
    with _clean_cache(), _declared(roots):
        registry = await ToolRegistry.load_content_roots(roots, session_id="ws")

    for name in registry.tools:
        assert registry.get(name) is not None, f"get({name!r}) returned None"
    assert await _call(registry, "official_only_tool") == "ONLY-OFFICIAL"


def test_r2_schedule_registry_still_takes_workspace_path() -> None:
    """R2 — schedules stay rooted at the workspace, not at a content root.

    Schedules are a mount-side question (whose reminders these are) and have no
    content-root answer; re-rooting them would silently drop every user's
    reminders. Read out of the shipping source so the criterion tracks the code
    rather than restating intent in prose.
    """
    source = (Path(tool_registry.__file__).parent / "agent.py").read_text(encoding="utf-8")

    assert 'ScheduleRegistry.load(\n            workspace_path / "schedules"' in source
    assert 'ScheduleRegistry.load(\n            agent_root / "schedules"' not in source
    assert "content_root" not in source.split("ScheduleRegistry.load(")[1].split(")")[0]


@pytest.mark.anyio
async def test_r3_asking_layer_beats_priority_for_private_helpers(tmp_path: Path) -> None:
    """R3 — A1's semantics survive the identity change.

    The official root's tool must keep reading the official helper even though a
    higher-priority root ships the same helper name. Priority orders cross-layer
    fallback only; it never overrides the asking layer's own implementation. The
    identity swap must not have relocated this.
    """
    official = tmp_path / "official"
    personal = tmp_path / "personal"
    official_tools = _write_root(official, "OFFICIAL")
    _write_root(personal, "PERSONAL")
    # Distinct public names so last-wins does not decide the outcome: both tools
    # stay reachable and each reports the helper its own layer resolved.
    (official_tools / "probe.py").write_text(
        "import _root_helper\n\n\nasync def official_probe() -> str:\n"
        '    """Report the helper the official layer resolved."""\n'
        "    return _root_helper.MARKER\n",
        encoding="utf-8",
    )

    roots = [
        ContentRoot(name="official", path=official, priority=0),
        ContentRoot(name="personal", path=personal, priority=10),
    ]
    with _clean_cache(), _declared(roots):
        registry = await ToolRegistry.load_content_roots(roots, session_id="ws")

    assert await _call(registry, "official_probe") == "OFFICIAL"
    assert await _call(registry, "probe_tool") == "PERSONAL"


# ── mutations: shipping behaviours switched off from the outside ───────────────

# Patched from the test rather than shipped as ``if MUTATION[...]`` branches, so
# the production path carries no test-only switches and a mutation cannot pass by
# being wired to a knob nobody reads — it has to replace real behaviour.


def _mutation_cache_key_drops_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Key on the hash alone — the削弱 this card is told to guard against.

    Found *by* the red-check: the layered path takes its id from the
    ``ContentRoot``, so no mutation of ``_layer_id`` reaches the cache key, and
    I1 had nothing attacking it until this existed.
    """
    monkeypatch.setattr(tool_registry, "_cache_key", lambda layer_id, file_hash: (file_hash,))


def _mutation_layer_id_is_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old placeholder, back: identity per mount instead of per content.

    Reaches the *single-dir* path only (``load()``), because ``load_content_roots``
    carries each root's declared id with it. So it reddens the ``_layer_id``
    criteria and not the load-level ones — recorded rather than papered over.
    """

    def by_path(tools_dir: Path, roots: Sequence[ContentRoot] | None = None) -> str:
        try:
            return str(tools_dir.resolve())
        except OSError:
            return str(tools_dir)

    monkeypatch.setattr(tool_registry, "_layer_id", by_path)


def _mutation_layer_id_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """One id for every dir — the hash-only key, in effect.

    Buys C1 by giving up isolation, which is the mistake worth having a guard
    for: it must redden the I-series.
    """
    monkeypatch.setattr(tool_registry, "_layer_id", lambda tools_dir, roots=None: "shared")


def _mutation_ignore_declared_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never consult the environment, so nothing is ever declared."""
    monkeypatch.setattr(cr, "content_roots_from_env", lambda env=None: [])
    monkeypatch.setattr(tool_registry, "content_roots_from_env", lambda env=None: [])


def _mutation_name_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Infer the name from the path instead of taking the declaration.

    The inference this module refuses to do. It survives C1 (one mount) and
    fails C2 (same content, two paths), which is the pair that pins identity to
    the declared name.
    """
    original = cr.ContentRoot.layer_id

    monkeypatch.setattr(
        cr.ContentRoot,
        "layer_id",
        property(lambda self: str(self.path.resolve())),
    )
    assert original is not None  # keeps the reference alive for readers


def _mutation_priority_descending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rank the first entry highest, so official would override personal."""
    real_parse = cr.parse_content_roots

    def reversed_priority(raw: str) -> list[ContentRoot]:
        roots = real_parse(raw)
        top = (len(roots) - 1) * 10
        return [ContentRoot(name=r.name, path=r.path, priority=top - r.priority) for r in roots]

    monkeypatch.setattr(cr, "parse_content_roots", reversed_priority)


def _mutation_accept_unnamed_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default a missing name instead of dropping the entry."""

    def lenient(raw: str) -> list[ContentRoot]:
        roots: list[ContentRoot] = []
        for piece in raw.split(os.pathsep):
            entry = piece.strip()
            if not entry:
                continue
            name, _, path_text = entry.partition("=")
            if not path_text:
                name, path_text = "default", entry
            roots.append(ContentRoot(name=name.strip(), path=Path(path_text.strip()), priority=len(roots) * 10))
        return roots

    monkeypatch.setattr(cr, "parse_content_roots", lenient)


MUTATIONS = {
    "cache_key_drops_layer": _mutation_cache_key_drops_layer,
    "layer_id_is_the_path": _mutation_layer_id_is_the_path,
    "layer_id_is_shared": _mutation_layer_id_is_shared,
    "ignore_declared_roots": _mutation_ignore_declared_roots,
    "name_from_path": _mutation_name_from_path,
    "priority_descending": _mutation_priority_descending,
    "accept_unnamed_roots": _mutation_accept_unnamed_roots,
}

# Which criteria each mutation must redden. Written as an exact set: a mutation
# that reddens *more* than listed is as much a finding as one that reddens less,
# because it means a criterion is coupled to something it does not claim to test.
EXPECTED_RED = {
    # The key is what the layered load actually reuses on, so this is the
    # mutation guarding I1 — and it must not touch the declaration criteria.
    "cache_key_drops_layer": {
        "test_i1_two_named_roots_with_identical_bytes_compile_twice",
    },
    # ``_layer_id`` serves the single-dir path (``load()``); the layered path
    # carries each root's declared id, so these two only reach C3/C4.
    # C5 goes red alongside C3 wherever the id itself is wrong: it asserts the
    # same value, only sourced from the environment instead of an argument.
    "layer_id_is_the_path": {
        "test_c3_layer_id_is_the_declared_name_not_a_path",
        "test_c5_layer_id_consults_the_declared_environment",
    },
    "layer_id_is_shared": {
        "test_c3_layer_id_is_the_declared_name_not_a_path",
        "test_c4_undeclared_dir_falls_back_to_its_own_path",
        "test_c5_layer_id_consults_the_declared_environment",
        "test_i2_distinct_names_keep_distinct_layer_ids",
    },
    # C1/C2 pass their roots in directly (the way ``agent.py`` does once the env
    # is read), so stubbing the env reader does not reach them; the criterion it
    # does reach is the one that reads the env.
    "ignore_declared_roots": {
        "test_c5_layer_id_consults_the_declared_environment",
    },
    "name_from_path": {
        "test_c2_same_content_at_two_paths_is_one_layer",
        "test_c3_layer_id_is_the_declared_name_not_a_path",
        "test_c5_layer_id_consults_the_declared_environment",
    },
    "priority_descending": {"test_p1_entries_parse_in_ascending_priority"},
    "accept_unnamed_roots": {"test_p3_unnamed_and_malformed_entries_are_dropped_not_guessed"},
}


@contextmanager
def mutated(knob: str) -> Iterator[None]:
    monkeypatch = pytest.MonkeyPatch()
    MUTATIONS[knob](monkeypatch)
    try:
        yield
    finally:
        monkeypatch.undo()


# The criteria the red-check drives, as name → callable. Only the ones any
# mutation claims to redden: a criterion nobody claims would silently never be
# exercised by the red-check, so keeping the two lists in step is asserted below.
def _red_check_targets() -> dict[str, Callable[..., Any]]:
    module = globals()
    return {name: module[name] for name in sorted({n for names in EXPECTED_RED.values() for n in names})}


def test_every_claimed_criterion_exists() -> None:
    """The mutation map may not name a criterion that is not in this file."""
    for knob, names in EXPECTED_RED.items():
        for name in names:
            assert name in globals(), f"{knob} claims to redden missing criterion {name!r}"


@pytest.mark.anyio
@pytest.mark.parametrize("knob", sorted(MUTATIONS))
async def test_mutations_redden_exactly_their_own_criteria(knob: str, tmp_path: Path) -> None:
    """Each mutation must break exactly the criteria it claims, and no others.

    This is the executable form of the variation review: if an implementation
    drift makes a criterion stop depending on the behaviour it names, this test
    fails rather than the criterion quietly passing forever.
    """
    targets = _red_check_targets()
    reddened: set[str] = set()

    for name, func in targets.items():
        case_dir = tmp_path / knob / name
        case_dir.mkdir(parents=True)
        with mutated(knob), _clean_cache():
            try:
                result = func(case_dir) if _takes_path(func) else func()
                if inspect.isawaitable(result):
                    await result
            except AssertionError:
                reddened.add(name)

    assert reddened == EXPECTED_RED[knob], (
        f"mutation {knob!r} reddened {sorted(reddened)}, expected {sorted(EXPECTED_RED[knob])}"
    )


def _takes_path(func: Callable[..., Any]) -> bool:
    """Whether a criterion takes the ``tmp_path`` fixture argument."""
    return "tmp_path" in inspect.signature(func).parameters
