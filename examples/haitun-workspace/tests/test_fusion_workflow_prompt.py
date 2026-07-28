"""Prompt contracts for the upper-layer FusionFlow workflow command."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Any

import anyio

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = WORKSPACE_ROOT / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

prompt_sections: Any = importlib.import_module("prompt_sections")
system: Any = importlib.import_module("system")


def _frontmatter_name(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if line == "---":
            break
        if line.startswith("name:"):
            return line.partition(":")[2].strip()
    raise AssertionError(f"missing frontmatter name in {path}")


def _async_tool_parameters(filename: str, function_name: str) -> list[str]:
    tree = ast.parse((WORKSPACE_ROOT / "tools" / filename).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name)
    return [argument.arg for argument in function.args.args]


def test_workflow_command_does_not_add_a_management_tool():
    assert "workflow_manage" not in prompt_sections.CORE_TOOL_SUMMARIES
    assert "workflow_manage" not in prompt_sections.TOOL_ORDER
    assert not (WORKSPACE_ROOT / "tools" / "workflow_manage.py").exists()
    schema_names = {schema["function"]["name"] for schema in system._build_self_evolution_tool_schemas()}
    assert "workflow_manage" not in schema_names
    assert "workflow_manage" not in system._SELF_EVOLUTION_PROMPT


async def test_exact_workflow_command_precedes_natural_language_routing():
    section = await system.System(anyio.Path(str(WORKSPACE_ROOT)))._build_fusion_section()

    assert _async_tool_parameters("run_flow.py", "run_flow") == [
        "flow_path",
        "inputs_json",
        "resource_capacities_json",
    ]
    command_index = section.index("### `/workflow:<slug>`")
    natural_language_index = section.index("### Natural-language activation")
    assert command_index < natural_language_index
    assert "matching exactly `/workflow:<slug>`" in section
    assert "Accept no suffix, inline parameters, or trailing" in section
    assert "argument syntax." in section
    assert "`flows/workflows/<slug>/<slug>.workflow`" in section
    inspect_index = section.index("Read that declaration before execution")
    collect_index = section.index("Resolve every required input from the conversation")
    call_index = section.index("call the existing `run_flow` runner")
    assert inspect_index < collect_index < call_index
    assert "end the turn without calling `run_flow`" in section
    assert "default empty input object" in section
    assert "exactly once for the initial execution, passing `flow_path` and the complete" in section
    assert "Use an empty input object only when the declaration requires" in section
    assert "`$fusion_flow/control`" in section
    assert "`run_flow_resume` once with the matching `run_id`, `request_id`" in section
    assert "Repeat only when that resume returns another Human" in section
    assert "adds no operator or manifest protocol" in section
    assert "must not route through" in section
    assert "`flow_manage`" in section
    assert "workflow_manage" not in section


async def test_registry_entries_are_not_injected_into_system_prompt(tmp_path: Path):
    workspace = anyio.Path(str(tmp_path))
    skill_dir = workspace / "skills" / "fusion-flow"
    await skill_dir.mkdir(parents=True)
    await (skill_dir / "SKILL.md").write_text(
        "---\nname: flow\ndescription: test\n---\n",
        encoding="utf-8",
    )

    registry_entry = workspace / "flows" / "workflows" / "private-workflow"
    await registry_entry.mkdir(parents=True)
    marker = "PRIVATE_WORKFLOW_SOURCE_MUST_NOT_ENTER_STABLE_PROMPT"
    await (registry_entry / "private-workflow.workflow").write_text(
        f"workflow private_workflow {{ -- {marker}\n}}\n",
        encoding="utf-8",
    )

    section = await system.System(workspace)._build_fusion_section()
    assert marker not in section


def test_fusion_skill_defines_only_the_exact_frontend_command():
    skill = WORKSPACE_ROOT / "skills" / "fusion-flow" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert _frontmatter_name(skill) == "flow"
    assert "/workflow:<slug>" in text
    assert "/workflow:daily-brief" in text
    assert "accept a suffix or" in text
    assert "flows/workflows/<slug>/<slug>.workflow" in text
    assert "existing file tools" in text
    assert "new save/list/load operator" in text
    assert "workflow_manage" not in text
    assert "JSON object]" not in text
    assert "Step may save a self-contained child declaration" in text
    assert text.index("inspect `input_workflow(...)`") < text.index("invoke `run_flow` exactly once")
    assert "end the turn without" in text
    assert "default empty input object" in text
    assert "Use an empty input object only when the declaration has no inputs." in text
    assert "resolve against the invoking psi workspace root" in text
