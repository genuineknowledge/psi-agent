from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

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
async def test_hermes_web_platform_prompt_advertises_file_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "hermes-style-workspace"
    _copy_workspace(REPO_ROOT / "examples" / "hermes-style-workspace", workspace)
    monkeypatch.setenv("HERMES_PLATFORM", "web")

    prompt = await _build_system_prompt_from_workspace(
        workspace,
        model="test-model",
        tool_names=["bash"],
    )

    assert "MEDIA:/absolute/path/to/file" in prompt
    assert "Do NOT tell the user you lack file-sending capability" in prompt


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


def _load_workspace_system(workspace: Path) -> Any:
    system_py = workspace / "systems" / "system.py"
    spec = importlib.util.spec_from_file_location("test_fusion_flow_workspace_system", system_py)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.System


@pytest.mark.anyio
async def test_fusion_flow_workspace_after_turn_skips_simple_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "fusion-flow-workspace"
    _copy_workspace(REPO_ROOT / "examples" / "fusion-flow-workspace", workspace)
    system_class = _load_workspace_system(workspace)
    system = system_class(workspace)

    calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def complete_fn(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        calls.append((messages, tools))
        return {"choices": [{"message": {"role": "assistant", "content": "Nothing to save."}}]}

    await system.after_turn(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        0,
        [],
        complete_fn=complete_fn,
        tool_executors={},
    )

    assert calls == []


@pytest.mark.anyio
async def test_fusion_flow_workspace_after_turn_can_create_curated_flow(tmp_path: Path) -> None:
    workspace = tmp_path / "fusion-flow-workspace"
    _copy_workspace(REPO_ROOT / "examples" / "fusion-flow-workspace", workspace)
    system_class = _load_workspace_system(workspace)
    system = system_class(workspace)

    callables = await load_tool_callables_from_workspace(workspace / "tools")
    complete_calls = 0

    async def complete_fn(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        nonlocal complete_calls
        complete_calls += 1
        assert tools is not None
        if complete_calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "flow_manage",
                                        "arguments": (
                                            '{"action":"create",'
                                            '"flow_name":"review-created-flow",'
                                            '"description":"Reusable review-created flow",'
                                            '"category":"review",'
                                            '"body":"Use this flow from tests.",'
                                            '"flow_ts":"export const marker = \\"review\\";"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Nothing to save."}}]}

    await system.after_turn(
        [
            {"role": "user", "content": "Build a reusable flow pattern."},
            {"role": "assistant", "content": "Created and validated the flow."},
        ],
        2,
        ["write", "bash"],
        complete_fn=complete_fn,
        tool_executors=callables,
    )

    flow_md = workspace / "flows" / "curated" / "review-created-flow" / "FLOW.md"
    assert complete_calls == 2
    assert flow_md.exists()
    flow_text = flow_md.read_text(encoding="utf-8")
    assert "Reusable review-created flow" in flow_text
    assert "export const marker = \"review\";" in flow_text
