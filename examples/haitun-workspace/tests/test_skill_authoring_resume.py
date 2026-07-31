"""Tests for skill_manage prefer-update / agent_editable resume base."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from psi_agent.session.runtime_context import path_scope

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
SKILLS_DIR = WORKSPACE_ROOT / "skills"


def _load(name: str):
    path = TOOLS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(TOOLS_DIR):
            sys.path.pop(0)
    return module


@pytest.mark.anyio
async def test_agent_editable_skill_can_be_patched(tmp_path):
    ws = tmp_path / "ws"
    agent = tmp_path / "agent"
    ws.mkdir()
    skills = agent / "skills" / "feishu-resume-review"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\n"
        "name: feishu-resume-review\n"
        "description: base\n"
        "category: productivity\n"
        "agent_editable: true\n"
        "---\n\n"
        "# old\n",
        encoding="utf-8",
    )
    tm = _load("skill_manage")
    with path_scope(workspace=str(ws), agent=str(agent)):
        out = await tm.skill_manage(
            action="patch",
            skill_name="feishu-resume-review",
            content="# new body\n\n## When to use\n\nx\n",
        )
    assert "patched" in out.lower()
    raw = (skills / "SKILL.md").read_text(encoding="utf-8")
    assert "new body" in raw
    assert "agent_editable: true" in raw
    assert "updated_at:" in raw


@pytest.mark.anyio
async def test_non_editable_skill_patch_rejected(tmp_path):
    ws = tmp_path / "ws"
    agent = tmp_path / "agent"
    ws.mkdir()
    skills = agent / "skills" / "example-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: x\ncategory: general\n---\n\n# hi\n",
        encoding="utf-8",
    )
    tm = _load("skill_manage")
    with path_scope(workspace=str(ws), agent=str(agent)):
        out = await tm.skill_manage(action="patch", skill_name="example-skill", content="# nope\n")
    assert "[Error]" in out
    assert "not agent-editable" in out


def test_resume_and_authoring_skills_exist():
    for name in ("feishu-resume-review", "skill-authoring-when", "skill-authoring-how"):
        path = SKILLS_DIR / name / "SKILL.md"
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "## When to use" in text
        assert "## When not to use" in text


def test_resume_skill_is_agent_editable():
    text = (SKILLS_DIR / "feishu-resume-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "agent_editable: true" in text
    assert "## 评分规则" in text
    assert "## 面试提问建议" in text


def test_authoring_when_requires_list_before_create():
    text = (SKILLS_DIR / "skill-authoring-when" / "SKILL.md").read_text(encoding="utf-8")
    assert "list" in text.lower()
    assert "patch" in text.lower()
    assert "自进化" in text
