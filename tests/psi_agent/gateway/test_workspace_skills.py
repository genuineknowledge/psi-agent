"""Stage 5 acceptance criteria -- GET /workspace/skills (content console backend).

The management console lists skills from two layers (official agent package +
global personal ~/.agent/skills); the global layer wins on name conflict and each
entry is tagged with a source. Because gateway cannot import the agent package,
this enumeration is a SECOND implementation of the merge logic -- so the key
criterion is CONSISTENCY: it must agree with agents/desktop _build_skills_index on
which layer wins (design doc "content layering" section 4, mitigation (a)).

Landing spot: src/psi_agent/gateway/desktop/_workspace_manager.py (list_skills +
module constant _GLOBAL_SKILLS_DIR) and _routes.py (handler + route). Tests follow
test_workspace_manager.py (wm = WorkspaceManager()) and reuse the importlib loader
from agents/desktop/tests/test_skill_layering.py for the agent-side cross-check.

Isolation: _GLOBAL_SKILLS_DIR is monkeypatched to tmp_path, never touching the
real ~/.agent.

Expected before implementation: 2 green (control: gateway import + agent system.py
loads) + 6 red (WorkspaceManager has no list_skills yet -- AttributeError).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import anyio
import pytest

from psi_agent.gateway.desktop import _workspace_manager as wm_mod
from psi_agent.gateway.desktop._workspace_manager import WorkspaceManager

REPO_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_SYSTEMS = REPO_ROOT / "agents" / "desktop" / "systems"


def _load_desktop_system(module_name: str):
    """Load agents/desktop/systems/system.py (mirrors Stage 1 test_skill_layering).

    system.py inserts its own systems/ + tools/ into sys.path on load, so the
    sibling imports (prompt_sections / _fusion_memory) resolve.
    """
    path = DESKTOP_SYSTEMS / "system.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(DESKTOP_SYSTEMS))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(DESKTOP_SYSTEMS):
            sys.path.pop(0)
    return module


def _write_skill(root: Path, name: str, description: str, category: str = "", body: str = "body") -> None:
    """Write root/skills/<name>/SKILL.md with frontmatter matching system.py's parser."""
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\n"
    if category:
        fm += f"category: {category}\n"
    fm += "---\n\n"
    (d / "SKILL.md").write_text(fm + body + "\n", encoding="utf-8")


def _find(skills: list[dict], name: str) -> dict:
    match = [s for s in skills if s["name"] == name]
    assert match, f"skill {name!r} not in {[s['name'] for s in skills]}"
    return match[0]


def _write_tombstone(global_root: Path, names: list[str]) -> None:
    """Write global_root/skill-tombstones.json (the sibling of global_root/skills).

    global_root is _GLOBAL_SKILLS_DIR's parent -- the gateway's ~/.agent stand-in.
    It is the SAME file and format the agent side reads
    (_GLOBAL_AGENT_HOME/skill-tombstones.json), which is what makes the G6
    consistency check meaningful. f-string JSON avoids a json import here.
    """
    global_root.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{n}"' for n in names)
    (global_root / "skill-tombstones.json").write_text(f'{{"disabled": [{listed}]}}', encoding="utf-8")


# -- Control cases (green before implementation: prove the harness works) -----


def test_control_wm_import() -> None:
    """Control: WorkspaceManager instantiates and has the existing read_file.

    Proves the gateway import path works. Mutation check: none (guard only).
    """
    wm = WorkspaceManager()
    assert hasattr(wm, "read_file")
    assert hasattr(wm, "reveal")


@pytest.mark.anyio
async def test_control_desktop_system_loads(request: pytest.FixtureRequest) -> None:
    """Control: agents/desktop/system.py loads and exposes _build_skills_index.

    Proves the agent-side cross-check harness works (the consistency criterion
    depends on it). Mutation check: none (guard only).
    """
    name = f"desktop_system_skillsctl_{id(request)}"
    system = _load_desktop_system(name)
    try:
        assert hasattr(system, "_build_skills_index")
    finally:
        sys.modules.pop(name, None)


# -- list_skills criteria (red before implementation) ------------------------


@pytest.mark.anyio
async def test_list_skills_official_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An official-only skill is listed with source="official".

    Mutation check: drop the official layer scan -- this turns red.
    """
    official = tmp_path / "agent"
    _write_skill(official, "foo", "OFFICIAL foo")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(tmp_path / "global" / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    entry = _find(skills, "foo")
    assert entry["source"] == "official"
    assert entry["description"] == "OFFICIAL foo"


@pytest.mark.anyio
async def test_list_skills_global_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A global-only skill is listed with source="global".

    Mutation check: drop the global layer scan -- this turns red.
    """
    official = tmp_path / "agent"
    (official / "skills").mkdir(parents=True)
    global_root = tmp_path / "global"
    _write_skill(global_root, "bar", "GLOBAL bar")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    entry = _find(skills, "bar")
    assert entry["source"] == "global"


@pytest.mark.anyio
async def test_list_skills_global_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same-name skill: the global personal layer wins (source + description).

    Mirrors agents/desktop _build_skills_index priority. Mutation check: scan
    global first then official (official overrides) -- this turns red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "foo", "OFFICIAL version")
    _write_skill(global_root, "foo", "GLOBAL version")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    entry = _find(skills, "foo")
    assert entry["source"] == "global"
    assert entry["description"] == "GLOBAL version"


@pytest.mark.anyio
async def test_list_skills_frontmatter_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """name / description / category come from frontmatter; path is the winning SKILL.md.

    Mutation check: skip frontmatter parsing (use dir name only) -- the category
    assertion turns red.
    """
    official = tmp_path / "agent"
    _write_skill(official, "foo", "the description", category="writing")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(tmp_path / "global" / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    entry = _find(skills, "foo")
    assert entry["description"] == "the description"
    assert entry["category"] == "writing"
    assert entry["path"].endswith("skills/foo/SKILL.md")


@pytest.mark.anyio
async def test_list_skills_empty_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both layers absent -> empty list (no crash). A missing ~/.agent is normal.

    Mutation check: make list_skills raise on a missing dir -- this turns red.
    """
    official = tmp_path / "agent"
    official.mkdir()
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(tmp_path / "no-global" / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    assert skills == []


@pytest.mark.anyio
async def test_list_skills_consistent_with_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """CONSISTENCY (the whole point): gateway list_skills and agents/desktop
    _build_skills_index must pick the SAME winning layer for a same-name skill.

    This guards the "second implementation" drift (design section 4): if the two
    disagree, the UI badge says one layer while the model actually uses another.
    Mutation check: flip only list_skills priority (not the index) -- this turns
    red while the pure-gateway criteria above stay green.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "foo", "OFFICIAL version")
    _write_skill(global_root, "foo", "GLOBAL version")

    # gateway side
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    gw = _find(skills, "foo")

    # agent side (same tmp dirs)
    name = f"desktop_system_consistency_{id(request)}"
    system = _load_desktop_system(name)
    try:
        monkeypatch.setattr(system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(global_root / "skills")))
        monkeypatch.setattr(system, "_GLOBAL_AGENT_HOME", anyio.Path(str(global_root)))
        xml = await system._build_skills_index(anyio.Path(str(official)))
    finally:
        sys.modules.pop(name, None)

    # both must resolve foo to the global personal layer
    assert gw["source"] == "global"
    assert gw["description"] == "GLOBAL version"
    assert "GLOBAL version" in xml
    assert "OFFICIAL version" not in xml


# -- delete_skill criteria (personal/global layer only; red before impl) ------
#
# delete_skill removes ONLY ~/.agent/skills/<name>. Official skills are never
# deleted here (read-only in production + restored on upgrade); hiding them is
# the tombstone mechanism (a later stage). These criteria lock that boundary.


@pytest.mark.anyio
async def test_delete_skill_removes_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting a personal skill removes its ~/.agent/skills/<name> directory.

    Mutation check: make delete_skill a no-op -- the exists() assertion turns red.
    """
    global_root = tmp_path / "global"
    _write_skill(global_root, "mine", "my skill")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    result = await WorkspaceManager().delete_skill("mine")
    assert result["ok"] is True
    assert not (global_root / "skills" / "mine").exists()


@pytest.mark.anyio
async def test_delete_skill_removes_subdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recursive delete: a skill with a scripts/ subdir is removed wholesale.

    Mutation check: replace the recursive delete with a single unlink -- this
    turns red (a non-empty dir cannot be unlinked / rmdir'd).
    """
    global_root = tmp_path / "global"
    _write_skill(global_root, "mine", "my skill")
    sub = global_root / "skills" / "mine" / "scripts"
    sub.mkdir(parents=True)
    (sub / "helper.py").write_text("print(1)", encoding="utf-8")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    await WorkspaceManager().delete_skill("mine")
    assert not (global_root / "skills" / "mine").exists()


@pytest.mark.anyio
async def test_delete_skill_never_touches_official(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_skill only removes the global layer; a same-name official skill survives.

    Mutation check: point delete_skill at the official layer too -- the official
    SKILL.md assertion turns red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "foo", "OFFICIAL foo")
    _write_skill(global_root, "foo", "GLOBAL foo")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    await WorkspaceManager().delete_skill("foo")
    assert not (global_root / "skills" / "foo").exists()  # global gone
    assert (official / "skills" / "foo" / "SKILL.md").exists()  # official intact


@pytest.mark.anyio
async def test_delete_skill_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A name with separators / .. / empty is rejected -- no escape from the global layer.

    Mutation check: drop the name validation -- ../escape would resolve outside
    the global dir and this turns red (no ValueError raised).
    """
    global_root = tmp_path / "global"
    (global_root / "skills").mkdir(parents=True)
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    wm = WorkspaceManager()
    for bad in ("../escape", "a/b", "..", "", "a\\b"):
        with pytest.raises(ValueError):
            await wm.delete_skill(bad)


@pytest.mark.anyio
async def test_delete_skill_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting an absent skill raises FileNotFoundError (not a silent no-op).

    Also covers an official-only name: it is absent from the global layer, so
    delete_skill cannot touch it. Mutation check: swallow the missing case and
    return ok -- this turns red.
    """
    global_root = tmp_path / "global"
    (global_root / "skills").mkdir(parents=True)
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    with pytest.raises(FileNotFoundError):
        await WorkspaceManager().delete_skill("nope")


# -- B: official-skill tombstone (disable/enable + list flagging; red before) --
#
# The tombstone file is _GLOBAL_SKILLS_DIR's parent / skill-tombstones.json, the
# same file agents/desktop reads. list_skills flags official-won entries only;
# disable_skill / enable_skill write that file. Consistency (G6) is the point.


@pytest.mark.anyio
async def test_list_skills_marks_tombstoned_official(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G1 (red before -> green after): an official skill in the tombstone is listed
    with tombstoned=true; an un-listed official skill stays false.

    Mutation check: make list_skills ignore the tombstone -- off-x's flag turns red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    _write_skill(official, "off-y", "OFFICIAL y")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    _write_tombstone(global_root, ["off-x"])
    skills = await WorkspaceManager().list_skills(str(official))
    assert _find(skills, "off-x").get("tombstoned") is True
    assert _find(skills, "off-y").get("tombstoned") is False


@pytest.mark.anyio
async def test_disable_skill_writes_tombstone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G2 (red before: disable_skill missing -> AttributeError; green after):
    disabling an official skill writes it to the tombstone file, and list_skills
    then reports tombstoned=true.

    Mutation check: make disable_skill a no-op -- the file / flag assertions turn red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    wm = WorkspaceManager()
    result = await wm.disable_skill("off-x")
    assert result["disabled"] is True
    assert (global_root / "skill-tombstones.json").exists()
    skills = await wm.list_skills(str(official))
    assert _find(skills, "off-x").get("tombstoned") is True


@pytest.mark.anyio
async def test_enable_skill_removes_tombstone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G3 (red before: enable_skill missing -> AttributeError; green after):
    enabling removes the name from the tombstone; list_skills clears the flag.

    Mutation check: make enable_skill a no-op -- the flag stays true, red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    _write_tombstone(global_root, ["off-x"])
    wm = WorkspaceManager()
    result = await wm.enable_skill("off-x")
    assert result["disabled"] is False
    skills = await wm.list_skills(str(official))
    assert _find(skills, "off-x").get("tombstoned") is False


@pytest.mark.anyio
async def test_list_skills_tombstone_spares_global_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G4 (red before -> green after): a same-name skill WON by the global layer is
    listed with source=global and tombstoned=false even though its name is in the
    tombstone -- the flag marks official-won entries only (mirrors agent B2).

    Mutation check: flag any tombstoned name regardless of source -- this turns red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "dup", "OFFICIAL version")
    _write_skill(global_root, "dup", "GLOBAL version")
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    _write_tombstone(global_root, ["dup"])
    skills = await WorkspaceManager().list_skills(str(official))
    entry = _find(skills, "dup")
    assert entry["source"] == "global"
    assert entry.get("tombstoned") is False


@pytest.mark.anyio
async def test_disable_skill_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G5 (red before: disable_skill missing -> AttributeError; green after):
    disable_skill validates the name like delete_skill (rejects separators / .. /
    empty), so junk cannot be injected into the tombstone list.

    Mutation check: drop disable_skill's name validation -- this turns red.
    """
    global_root = tmp_path / "global"
    (global_root / "skills").mkdir(parents=True)
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    wm = WorkspaceManager()
    for bad in ("../escape", "a/b", "..", "", "a\\b"):
        with pytest.raises(ValueError):
            await wm.disable_skill(bad)


@pytest.mark.anyio
async def test_tombstone_consistent_agent_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """G6 CONSISTENCY (the whole point of B): the official skills the agent index
    DROPS must be exactly the ones gateway list_skills FLAGS as tombstoned. Both
    sides read the SAME file (global_root/skill-tombstones.json).

    Guards the second-implementation drift for the tombstone. Mutation check: make
    gateway flag a different set than the index drops -- this turns red.
    """
    official = tmp_path / "agent"
    global_root = tmp_path / "global"
    _write_skill(official, "off-x", "OFFICIAL x")
    _write_skill(official, "off-y", "OFFICIAL y")
    _write_tombstone(global_root, ["off-x"])

    # gateway side: flags off-x
    monkeypatch.setattr(wm_mod, "_GLOBAL_SKILLS_DIR", str(global_root / "skills"), raising=False)
    skills = await WorkspaceManager().list_skills(str(official))
    gw_tombstoned = {s["name"] for s in skills if s.get("tombstoned")}

    # agent side: same tmp dirs + same tombstone file -> index drops off-x
    name = f"desktop_system_tombstone_{id(request)}"
    system = _load_desktop_system(name)
    try:
        monkeypatch.setattr(system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(global_root / "skills")))
        monkeypatch.setattr(system, "_GLOBAL_AGENT_HOME", anyio.Path(str(global_root)))
        xml = await system._build_skills_index(anyio.Path(str(official)))
    finally:
        sys.modules.pop(name, None)

    assert gw_tombstoned == {"off-x"}  # gateway flags exactly off-x
    assert "OFFICIAL x" not in xml  # agent index dropped off-x
    assert "OFFICIAL y" in xml  # agent index kept off-y
