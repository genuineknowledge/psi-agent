from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from psi_agent.session import _build_system_prompt_from_workspace
from psi_agent.session.tools import load_tool_callables_from_workspace, load_tools_from_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy_workspace(src: Path, dst: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> list[str]:
        return [name for name in names if name == "__pycache__" or name.endswith(".pyc")]

    shutil.copytree(src, dst, ignore=ignore)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("workspace_name", "expected_tools", "prompt_markers"),
    [
        (
            "openclaw-style-workspace",
            {"bash", "edit", "memory_read", "memory_write", "read", "web_search", "write"},
            {"OPENCLAW_CACHE_BOUNDARY", "Tool Call Style"},
        ),
        (
            "hermes-style-workspace",
            {"bash", "computer_use", "kanban_show", "memory", "session_search", "skill_manage"},
            {"Conversation started:", "Skills"},
        ),
        (
            "fusion-flow-workspace",
            {"bash", "edit", "flow_manage", "read", "skill_manage", "write"},
            {
                "Fusion Flow Trigger",
                "Self-Evolution Tools",
                "flow_manage",
                "skill_manage",
                "skills/fusion-flow/SKILL.md",
                "flows/<task-slug>",
                "runsDir",
                "FLOW_ENGINE=psi",
            },
        ),
    ],
)
async def test_migrated_example_workspace_loads_system_prompt_and_tools(
    tmp_path: Path,
    workspace_name: str,
    expected_tools: set[str],
    prompt_markers: set[str],
) -> None:
    workspace = tmp_path / workspace_name
    _copy_workspace(REPO_ROOT / "examples" / workspace_name, workspace)

    tools = await load_tools_from_workspace(workspace / "tools")
    callables = await load_tool_callables_from_workspace(workspace / "tools")
    prompt = await _build_system_prompt_from_workspace(
        workspace,
        model="test-model",
        tool_names=sorted(tools),
    )

    assert expected_tools.issubset(tools)
    assert set(tools) == set(callables)
    assert prompt is not None
    assert len(prompt) > 1000
    for marker in prompt_markers:
        assert marker in prompt


@pytest.mark.anyio
async def test_fusion_flow_workspace_self_evolution_tools_manage_agent_assets(tmp_path: Path) -> None:
    workspace = tmp_path / "fusion-flow-workspace"
    _copy_workspace(REPO_ROOT / "examples" / "fusion-flow-workspace", workspace)

    callables = await load_tool_callables_from_workspace(workspace / "tools")
    skill_manage = callables["skill_manage"]
    flow_manage = callables["flow_manage"]

    skill_created = await skill_manage(
        action="create",
        skill_name="agent-test-skill",
        description="Reusable test procedure",
        category="test",
        content="Use this only in tests.",
    )
    assert "Skill created" in skill_created
    skill_md = workspace / "skills" / "agent-test-skill" / "SKILL.md"
    assert skill_md.exists()
    assert "created_by: agent" in skill_md.read_text(encoding="utf-8")

    user_skill_patch = await skill_manage(
        action="patch",
        skill_name="fusion-flow",
        content="should not replace the runtime skill",
    )
    assert "read-only" in user_skill_patch

    flow_created = await flow_manage(
        action="create",
        flow_name="agent-test-flow",
        description="Reusable test flow",
        category="test",
        body="Use this flow only in tests.",
        flow_ts="export const marker = 'test';",
    )
    assert "Curated flow created" in flow_created
    flow_md = workspace / "flows" / "curated" / "agent-test-flow" / "FLOW.md"
    assert flow_md.exists()
    flow_text = flow_md.read_text(encoding="utf-8")
    assert "created_by: agent" in flow_text
    assert "export const marker = 'test';" in flow_text

    prompt = await _build_system_prompt_from_workspace(
        workspace,
        model="test-model",
        tool_names=sorted(callables),
    )
    assert prompt is not None
    assert "agent-test-flow" in prompt
    assert "Reusable test flow" in prompt
