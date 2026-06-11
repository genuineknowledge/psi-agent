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
            {"bash", "edit", "read", "write"},
            {"Fusion Flow Trigger", "skills/fusion-flow/SKILL.md", "flows/<task-slug>", "runsDir", "FLOW_ENGINE=psi"},
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
