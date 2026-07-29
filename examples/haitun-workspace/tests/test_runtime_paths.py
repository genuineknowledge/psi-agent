"""Step 3 — workspace/agent path resolution via ContextVars."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from psi_agent.session.runtime_context import path_scope

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"


def _load(name: str):
    path = TOOLS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Ensure sibling imports (``_runtime_paths``) resolve from tools/.
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(TOOLS_DIR):
            sys.path.pop(0)
    return module


@pytest.fixture
def paths():
    return _load("_runtime_paths")


def test_workspace_prefers_contextvar_over_env(paths, monkeypatch, tmp_path):
    ws = tmp_path / "user-ws"
    agent = tmp_path / "agent-pkg"
    ws.mkdir()
    agent.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "from-env"))
    with path_scope(workspace=str(ws), agent=str(agent)):
        assert paths.workspace_dir() == str(ws)
        assert paths.agent_dir() == str(agent)


def test_agent_falls_back_to_workspace_when_empty(paths, tmp_path):
    ws = tmp_path / "only-ws"
    ws.mkdir()
    with path_scope(workspace=str(ws), agent=""):
        assert paths.agent_dir() == str(ws)


def test_explicit_arg_wins(paths, tmp_path):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    with path_scope(workspace=str(tmp_path / "ctx"), agent=str(tmp_path / "ag")):
        assert paths.workspace_dir(str(explicit)) == str(explicit)
        assert paths.agent_dir(str(explicit)) == str(explicit)


def test_resolve_user_path_relative_and_absolute(paths, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with path_scope(workspace=str(ws), agent=str(ws)):
        rel = paths.resolve_user_path("notes/a.md")
        assert Path(str(rel)) == ws / "notes" / "a.md"
        abs_target = tmp_path / "outside.txt"
        got = paths.resolve_user_path(str(abs_target))
        assert Path(str(got)) == abs_target


@pytest.mark.anyio
async def test_write_relative_lands_in_workspace(tmp_path):
    write_mod = _load("write")
    ws = tmp_path / "ws"
    agent = tmp_path / "agent"
    ws.mkdir()
    agent.mkdir()
    with path_scope(workspace=str(ws), agent=str(agent)):
        msg = await write_mod.write("out/hello.txt", "hi")
    assert "hello.txt" in msg
    assert (ws / "out" / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert not (agent / "out").exists()


@pytest.mark.anyio
async def test_skill_manage_uses_agent_root(tmp_path):
    skill_mod = _load("skill_manage")
    ws = tmp_path / "ws"
    agent = tmp_path / "agent"
    ws.mkdir()
    agent.mkdir()
    (agent / "skills").mkdir()
    with path_scope(workspace=str(ws), agent=str(agent)):
        skills = skill_mod._skills_dir()
    assert Path(str(skills)) == agent / "skills"
