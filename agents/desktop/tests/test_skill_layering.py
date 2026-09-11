"""Stage 1 acceptance criteria -- skill official layer + global personal layer
layering, where the global personal layer wins.

Maps to the ToC skill layering plan section 5 (A/C/D/E) and task-book criteria
1/2/3/5/6/8. Landing spot is agents/desktop (per the team lead; agents/feishu is
not touched).

Two sides, both with effective priority "global personal layer highest", but
iterating in opposite directions (criterion 8):

- Index side, systems/system.py _build_skills_index: scans official first, then
  global overrides (last write wins) -- decides which copy the model "sees".
- Body side, tools/_runtime_paths.py resolve_skill_path (added by C): queries
  global first, official as fallback (first hit wins) -- decides which layer the
  model's read hits.

Loading follows agents/feishu/tests/test_system_agent_root.py (importlib from
systems/) and test_runtime_paths.py (importlib from tools/ + the kernel
path_scope). agents/desktop/tests has no conftest, so every isolation fixture is
defined in this file.

Isolation: the global-layer constants (_GLOBAL_AGENT_SKILLS_DIR /
_GLOBAL_AGENT_HOME) are always monkeypatched to tmp_path, never reading or
writing the real ~/.agent and never depending on whether it exists.

Expected before implementation: 4 green (control/guard, proving the harness
works) plus red for the rest (A index flip, C resolve_skill_path not present).
An error (not a failure) usually means system.py failed to load -- paste it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import anyio
import pytest

from psi_agent.session.runtime_context import path_scope

AGENT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = AGENT_ROOT / "systems"
TOOLS = AGENT_ROOT / "tools"


def _load(module_name: str, path: Path, sys_path_dir: Path):
    """Load an agent-package module in place via importlib.

    Mirrors agents/feishu/tests/test_system_agent_root.py. sys_path_dir goes on
    sys.path temporarily so the module's sibling imports (prompt_sections /
    _runtime_paths / _fusion_memory) resolve.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(sys_path_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(sys_path_dir):
            sys.path.pop(0)
    return module


@pytest.fixture(autouse=True)
def _isolate_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate AppData: history/todo IO triggered by loading system.py lands in tmp."""
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / ".psi-appdata"))


@pytest.fixture
def system(request: pytest.FixtureRequest):
    name = f"desktop_system_layering_{id(request)}"
    module = _load(name, SYSTEMS / "system.py", SYSTEMS)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture
def paths(request: pytest.FixtureRequest):
    name = f"desktop_runtime_paths_layering_{id(request)}"
    module = _load(name, TOOLS / "_runtime_paths.py", TOOLS)
    yield module
    sys.modules.pop(name, None)


def _write_skill(root: Path, name: str, description: str, body: str = "body") -> None:
    """Write a skill with frontmatter at root/skills/<name>/SKILL.md.

    The frontmatter format matches system.py's parser (--- opener, key: val lines).
    """
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _point_global_at(system, monkeypatch: pytest.MonkeyPatch, global_root: Path) -> None:
    """Redirect the index-side global-layer constants to tmp (incl. the
    _GLOBAL_AGENT_HOME snapshot target after D)."""
    monkeypatch.setattr(system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(global_root / "skills")))
    monkeypatch.setattr(system, "_GLOBAL_AGENT_HOME", anyio.Path(str(global_root)))


def _write_tombstone(global_root: Path, names: list[str]) -> None:
    """Write the tombstone file at _GLOBAL_AGENT_HOME/skill-tombstones.json.

    The format is the agent<->gateway shared contract: {"disabled": [names]}.
    Built with an f-string to avoid a json import here; names are plain skill
    dir names, so no escaping is needed.
    """
    listed = ", ".join(f'"{n}"' for n in names)
    global_root.mkdir(parents=True, exist_ok=True)  # the home may not exist yet (index not run)
    (global_root / "skill-tombstones.json").write_text(f'{{"disabled": [{listed}]}}', encoding="utf-8")


# -- Index side (system._build_skills_index) --------------------------------


@pytest.mark.anyio
async def test_control_official_only_visible(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control (green before implementation): an official-only skill is indexed.

    Proves system.py loads, _build_skills_index is callable, the harness is sound.
    Mutation check: make _collect_skill_dirs always return [] -- this turns red.
    """
    official = tmp_path / "agent"
    _write_skill(official, "foo", "OFFICIAL foo")
    _point_global_at(system, monkeypatch, tmp_path / "global")
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "OFFICIAL foo" in xml


@pytest.mark.anyio
async def test_judge3_global_only_visible(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 3 (green before, guard): a global-only skill is indexed.

    _build_skills_index already scans the global layer (system.py:526); the A
    priority flip must not break global-only visibility.
    Mutation check: delete the loop that scans the global layer -- this turns red.
    """
    official = tmp_path / "agent"
    (official / "skills").mkdir(parents=True)
    global_root = tmp_path / "global"
    _write_skill(global_root, "bar", "GLOBAL bar")
    _point_global_at(system, monkeypatch, global_root)
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL bar" in xml


@pytest.mark.anyio
async def test_judge1_same_name_global_wins(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 1 (red before -> green after A): official + global same name,
    the index shows the global description.

    Before A (system.py:526-529 scans global, official overrides) it shows the
    official one -> red. After A swaps the two loops -> green.
    Mutation check: swap A's two loops back (global first, official last) -- red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "foo", "OFFICIAL version")
    _write_skill(global_root, "foo", "GLOBAL version")
    _point_global_at(system, monkeypatch, global_root)
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL version" in xml
    assert "OFFICIAL version" not in xml


@pytest.mark.anyio
async def test_judge6_official_replace_keeps_global(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 6 (red before -> green after A): the official layer is replaced
    on upgrade, the global same-name skill still wins.

    Simulates requirement one: an upgrade replaces the official dir wholesale; the
    user's global same-name skill is unaffected.
    Mutation check: same as criterion 1 (swap A's loops back) -- red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "foo", "OFFICIAL v1")
    _write_skill(global_root, "foo", "GLOBAL version")
    _point_global_at(system, monkeypatch, global_root)
    _write_skill(official, "foo", "OFFICIAL v2")  # official layer "replaced by upgrade"
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL version" in xml
    assert "OFFICIAL v2" not in xml


@pytest.mark.anyio
async def test_judge5_global_change_picked_up(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 5 (green before, guard): editing a global file then rebuilding
    the index reflects the new content.

    Relies on the manifest content hash invalidating the snapshot cache
    (system.py:553), not a new mechanism; the kernel rebuilds naturally per turn.
    Mutation check: hardcode the manifest to a constant (cache never invalidates)
    -- the second assertion (GLOBAL v2) turns red.
    """
    official = tmp_path / "agent"
    (official / "skills").mkdir(parents=True)
    global_root = tmp_path / "global"
    _write_skill(global_root, "baz", "GLOBAL v1")
    _point_global_at(system, monkeypatch, global_root)
    xml1 = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL v1" in xml1
    _write_skill(global_root, "baz", "GLOBAL v2")  # user edited the skill before next turn
    xml2 = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL v2" in xml2
    assert "GLOBAL v1" not in xml2


# -- Body side (paths.resolve_skill_path, present only after C) --------------


def test_path_control_resolve_user_path(paths, tmp_path: Path) -> None:
    """Control (green before): the existing resolve_user_path resolves a relative
    path under the workspace. Proves _runtime_paths loads and path_scope works.
    Does not depend on C.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    with path_scope(workspace=str(ws), agent=str(tmp_path / "agent")):
        got = paths.resolve_user_path("notes/a.md")
    assert Path(str(got)) == ws / "notes" / "a.md"


@pytest.mark.anyio
async def test_path_judge2_official_only_readable(paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 2 (red before: resolve_skill_path missing -> green after C): an
    official-only skill body resolves to the official layer.

    The global layer lacks this skill -> falls back to official.
    Mutation check: reverse resolve_skill_path's layer order (official first) --
    this stays green but test_path_global_wins turns red (together they lock the
    direction).
    """
    monkeypatch.setattr(paths, "_GLOBAL_AGENT_HOME", str(tmp_path / "global"), raising=False)
    official = tmp_path / "agent"
    _write_skill(official, "foo", "OFFICIAL foo")
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = await paths.resolve_skill_path("skills/foo/SKILL.md")
    assert Path(str(got)) == official / "skills" / "foo" / "SKILL.md"


@pytest.mark.anyio
async def test_path_global_wins(paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 8 body side (red before -> green after C): same-name skill,
    resolve_skill_path hits the global layer first.

    Mutation check: reverse the layer order (official first) -- red (hits official
    instead of global).
    """
    global_root = tmp_path / "global"
    official = tmp_path / "agent"
    _write_skill(global_root, "foo", "GLOBAL foo")
    _write_skill(official, "foo", "OFFICIAL foo")
    monkeypatch.setattr(paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = await paths.resolve_skill_path("skills/foo/SKILL.md")
    assert Path(str(got)) == global_root / "skills" / "foo" / "SKILL.md"


@pytest.mark.anyio
async def test_path_support_file_same_layer(paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-specific (red before -> green after C): a skill's sibling support file
    also goes through layered resolution and hits the same layer.

    This is why C (branch on the skills/ prefix at the read side) beats the "index
    emits <location>" alternative: scripts/ references/ are covered too.
    Mutation check: make read.py branch only for SKILL.md, not the skills/ prefix
    -- red.
    """
    global_root = tmp_path / "global"
    official = tmp_path / "agent"
    d = global_root / "skills" / "foo" / "scripts"
    d.mkdir(parents=True)
    (d / "helper.py").write_text("print(1)", encoding="utf-8")
    _write_skill(official, "foo", "OFFICIAL foo")
    monkeypatch.setattr(paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = await paths.resolve_skill_path("skills/foo/scripts/helper.py")
    assert Path(str(got)) == global_root / "skills" / "foo" / "scripts" / "helper.py"


@pytest.mark.anyio
async def test_path_absolute_passthrough(paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-specific (red before -> green after C): an absolute path passes through
    unchanged, no layering.

    Mutation check: drop resolve_skill_path's is_absolute branch -- red.
    """
    monkeypatch.setattr(paths, "_GLOBAL_AGENT_HOME", str(tmp_path / "global"), raising=False)
    official = tmp_path / "agent"
    official.mkdir()
    abs_target = tmp_path / "outside.txt"
    abs_target.write_text("x", encoding="utf-8")
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = await paths.resolve_skill_path(str(abs_target))
    assert Path(str(got)) == abs_target


@pytest.mark.anyio
async def test_path_nonexistent_defaults_to_official(paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-specific (red before -> green after C): neither layer exists -> return
    the official-layer path (stable error message).

    read.py then fails exists() and returns "[Error] File not found: <official
    path>", stable for debugging.
    Mutation check: change the fallback to return the global path -- red.
    """
    monkeypatch.setattr(paths, "_GLOBAL_AGENT_HOME", str(tmp_path / "global"), raising=False)
    official = tmp_path / "agent"
    official.mkdir()
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = await paths.resolve_skill_path("skills/nope/SKILL.md")
    assert Path(str(got)) == official / "skills" / "nope" / "SKILL.md"


# -- Criterion 8: index and body hit the same layer (cross-side consistency) --


@pytest.mark.anyio
async def test_judge8_index_and_path_agree(system, paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 8 (red before -> green after A+C): for a same-name skill, the
    index and the body must hit the same layer.

    Index forward (last wins), body reverse (first wins) -- opposite iteration but
    the same effective priority (global highest). This welds the two sides:
    reversing either one causes an "index says global, body reads official"
    mismatch. Mutation check: flip only A without C (or vice versa) -- red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "foo", "OFFICIAL version")
    _write_skill(global_root, "foo", "GLOBAL version")
    _point_global_at(system, monkeypatch, global_root)  # index-side global layer
    monkeypatch.setattr(paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)  # body-side same tmp
    xml = await system._build_skills_index(anyio.Path(str(official)))
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = await paths.resolve_skill_path("skills/foo/SKILL.md")
    assert "GLOBAL version" in xml  # index: global wins
    assert Path(str(got)) == global_root / "skills" / "foo" / "SKILL.md"  # body: global wins


# -- E: skill_manage landing spot and layering (user creates/views skills) ----


@pytest.fixture
def skill_manage_mod(request: pytest.FixtureRequest):
    name = f"desktop_skill_manage_layering_{id(request)}"
    module = _load(name, TOOLS / "skill_manage.py", TOOLS)
    yield module
    sys.modules.pop(name, None)
    sys.modules.pop("_runtime_paths", None)  # sibling skill_manage imports by standard name


@pytest.mark.anyio
async def test_skill_manage_dir_is_global(skill_manage_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E criterion (red before -> green after): _skills_dir lands in the global
    personal layer, not the official package.

    Mutation check: change _skills_dir back to resolve_agent()/"skills" -- red.
    """
    global_root = tmp_path / "global"
    official = tmp_path / "agent"
    official.mkdir()
    monkeypatch.setattr(skill_manage_mod._paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        got = skill_manage_mod._skills_dir()
    assert Path(str(got)) == global_root / "skills"


@pytest.mark.anyio
async def test_skill_manage_list_merges_layers(
    skill_manage_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E criterion (red before -> green after): list merges official + global,
    global wins on conflict.

    Scanning only the global layer would make the pre-create dedup miss official
    skills, so list must scan both layers.
    Mutation check: make list scan only skills_dir (global) -- the OFFICIAL-only
    assertion turns red.
    """
    global_root = tmp_path / "global"
    official = tmp_path / "agent"
    _write_skill(official, "foo", "OFFICIAL foo")
    _write_skill(official, "officialonly", "OFFICIAL only")
    _write_skill(global_root, "foo", "GLOBAL foo")
    monkeypatch.setattr(skill_manage_mod._paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        out = await skill_manage_mod.skill_manage(action="list")
    assert "GLOBAL foo" in out  # same name, global wins
    assert "OFFICIAL foo" not in out
    assert "OFFICIAL only" in out  # official-only still visible


@pytest.mark.anyio
async def test_skill_manage_view_resolves_official(
    skill_manage_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E guard (green before and after; turns red if _skills_dir is changed
    without changing view): an official-only skill is still viewable (goes through
    resolve_skill_path, not misled by _skills_dir landing in the global layer).

    Mutation check: make view use skills_dir/skill_name (global) instead of
    resolve_skill_path -- red.
    """
    global_root = tmp_path / "global"
    official = tmp_path / "agent"
    _write_skill(official, "foo", "OFFICIAL foo", body="official body marker")
    monkeypatch.setattr(skill_manage_mod._paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        out = await skill_manage_mod.skill_manage(action="view", skill_name="foo")
    assert "official body marker" in out


@pytest.mark.anyio
async def test_skill_manage_create_lands_global(
    skill_manage_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E criterion (red before -> green after): create writes to the global
    personal layer, not the official package (requirement one closed loop).

    Mutation check: change _skills_dir back to the official layer -- create lands
    in official, the global assertion turns red.
    """
    global_root = tmp_path / "global"
    official = tmp_path / "agent"
    official.mkdir()
    monkeypatch.setattr(skill_manage_mod._paths, "_GLOBAL_AGENT_HOME", str(global_root), raising=False)
    with path_scope(workspace=str(tmp_path / "ws"), agent=str(official)):
        await skill_manage_mod.skill_manage(
            action="create", skill_name="newone", content="hello body", description="new skill"
        )
    assert (global_root / "skills" / "newone" / "SKILL.md").exists()
    assert not (official / "skills" / "newone").exists()


# -- B: official-skill tombstone (disable without deleting the read-only pkg) --


@pytest.mark.anyio
async def test_tombstone_hides_listed_official_only(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B1' (red before -> green after the tombstone filter): a tombstoned
    official skill is dropped from the index while an un-listed official skill
    stays. Proves the filter is precise (only listed names, only official-won).

    Mutation check: remove the tombstone filter -- off-x reappears, red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    _write_skill(official, "off-y", "OFFICIAL y")
    _point_global_at(system, monkeypatch, global_root)
    _write_tombstone(global_root, ["off-x"])
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "OFFICIAL y" in xml
    assert "OFFICIAL x" not in xml


@pytest.mark.anyio
async def test_tombstone_invalidates_snapshot(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B7 (red before -> green after): tombstoning AFTER a snapshot was written
    must invalidate the cache. The filter sits before the manifest is computed,
    so the disabled skill leaves the manifest -> snapshot misses -> rebuild.

    This is the crux: if the filter ran after the snapshot-hit return, the stale
    cache would keep serving the disabled skill. Two skills are used so the index
    stays non-empty after filtering (a single skill would return early at the
    empty-entries guard and never reach the snapshot logic). Mutation check:
    move the filter below the snapshot read -- xml2 still serves off-x, red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    _write_skill(official, "off-y", "OFFICIAL y")
    _point_global_at(system, monkeypatch, global_root)
    xml1 = await system._build_skills_index(anyio.Path(str(official)))
    assert "OFFICIAL x" in xml1 and "OFFICIAL y" in xml1  # snapshot written with both
    _write_tombstone(global_root, ["off-x"])  # user disables off-x after the fact
    xml2 = await system._build_skills_index(anyio.Path(str(official)))
    assert "OFFICIAL x" not in xml2  # stale snapshot must NOT serve the disabled skill
    assert "OFFICIAL y" in xml2  # the survivor is rebuilt


@pytest.mark.anyio
async def test_tombstone_spares_global_override(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B2 (control, green before and after): a tombstoned NAME whose winner is
    the GLOBAL layer (a user-derived override) is NOT hidden -- the tombstone
    only stops official-won skills. Guards against the filter over-reaching.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "dup", "OFFICIAL version")
    _write_skill(global_root, "dup", "GLOBAL version")
    _point_global_at(system, monkeypatch, global_root)
    _write_tombstone(global_root, ["dup"])
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL version" in xml  # global wins, tombstone does not touch it


@pytest.mark.anyio
async def test_tombstone_empty_hides_nothing(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B3 (control, green before and after): an empty tombstone list hides
    nothing -- the official skill stays indexed."""
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    _point_global_at(system, monkeypatch, global_root)
    _write_tombstone(global_root, [])
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "OFFICIAL x" in xml


@pytest.mark.anyio
async def test_tombstone_spares_pure_global(system, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B4 (control, green before and after): a pure personal skill (global-only)
    listed in the tombstone is still indexed -- personal skills are removed via
    the physical delete (A), not the tombstone."""
    official = tmp_path / "agent"
    (official / "skills").mkdir(parents=True)
    global_root = tmp_path / "global"
    _write_skill(global_root, "mine", "GLOBAL mine")
    _point_global_at(system, monkeypatch, global_root)
    _write_tombstone(global_root, ["mine"])
    xml = await system._build_skills_index(anyio.Path(str(official)))
    assert "GLOBAL mine" in xml
