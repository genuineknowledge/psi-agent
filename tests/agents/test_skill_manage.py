"""Unit tests for skill_manage meta tool (list, view, create, patch, delete)."""

from __future__ import annotations

import os
from pathlib import Path

import anyio
import pytest


@pytest.fixture
def skill_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    official = tmp_path / "official"
    writable = tmp_path / "writable"
    (official / "skills").mkdir(parents=True)
    (writable / "skills").mkdir(parents=True)

    monkeypatch.setenv("PSI_CONTENT_ROOTS", f"official={official};writable={writable}")

    empty_agent = tmp_path / "agent"
    (empty_agent / "skills").mkdir(parents=True)

    import _content_layers
    import _runtime_paths

    monkeypatch.setattr(_runtime_paths, "agent_dir", lambda raw="": str(empty_agent))
    monkeypatch.setattr(_content_layers._paths, "resolve_agent", lambda raw="": anyio.Path(str(empty_agent)))

    return tmp_path


@pytest.mark.anyio
async def test_skill_manage_create_view_list_patch_delete(skill_env: Path) -> None:
    from skill_manage import skill_manage

    # 1. Create a new skill
    res = await skill_manage(
        action="create",
        skill_name="my-test-skill",
        content="This is the skill body.",
        category="testing",
        description="A test skill",
    )
    assert "Skill created: 'my-test-skill'" in res

    # 2. View the skill
    view_res = await skill_manage(action="view", skill_name="my-test-skill")
    assert "name: my-test-skill" in view_res
    assert "This is the skill body." in view_res

    # 3. List skills
    list_res = await skill_manage(action="list")
    assert "my-test-skill" in list_res

    # 4. Patch the skill
    patch_res = await skill_manage(
        action="patch",
        skill_name="my-test-skill",
        content="Updated skill body content.",
    )
    assert "Skill patched: 'my-test-skill'" in patch_res

    updated_view = await skill_manage(action="view", skill_name="my-test-skill")
    assert "Updated skill body content." in updated_view

    # 5. Delete the skill
    del_res = await skill_manage(action="delete", skill_name="my-test-skill")
    assert "Skill deleted: 'my-test-skill'" in del_res

    # View after delete should return error
    view_after = await skill_manage(action="view", skill_name="my-test-skill")
    assert "[Error] Skill not found" in view_after
