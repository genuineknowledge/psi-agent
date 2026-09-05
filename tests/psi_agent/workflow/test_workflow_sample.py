from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from psi_agent.session.agent import SessionAgent
from psi_agent.session.runtime_context import path_scope, runtime_scope

REPO_ROOT = Path(__file__).resolve().parents[3]
FEISHU_SKILL = REPO_ROOT / "agents" / "feishu" / "skills" / "workflow"
DESKTOP_SKILL = REPO_ROOT / "agents" / "desktop" / "skills" / "workflow"
TOOLS_DIR = REPO_ROOT / "agents" / "feishu" / "tools"


def _load_tool(skill_dir: Path):
    path = skill_dir / "workflow_sample.py"
    name = f"workflow_sample_test_{skill_dir.parent.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(skill_dir))
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        for directory in (str(TOOLS_DIR), str(skill_dir)):
            if directory in sys.path:
                sys.path.remove(directory)
    return module


def _load_runner():
    path = TOOLS_DIR / "run_flow.py"
    name = "workflow_run_flow_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(FEISHU_SKILL))
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        for directory in (str(TOOLS_DIR), str(FEISHU_SKILL)):
            if directory in sys.path:
                sys.path.remove(directory)
    return module


@pytest.mark.anyio
async def test_record_preserves_versions_and_exact_user_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = anyio.Path(tmp_path / "workspace")
    flow_path = workspace / "flows" / "review.workflow"
    await flow_path.parent.mkdir(parents=True)
    await flow_path.write_text("workflow version one\n", encoding="utf-8")
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / "appdata"))
    tool = _load_tool(FEISHU_SKILL)

    with path_scope(workspace=str(workspace), agent=str(REPO_ROOT / "agents" / "feishu")):
        initial_result = json.loads(
            await tool.workflow_sample_record(
                "flows/review.workflow",
                ["Run two reviews", "Combine their findings"],
                question="Review this repository.",
            )
        )
        await flow_path.write_text("workflow version two\n", encoding="utf-8")
        adjustment_result = json.loads(
            await tool._record_workflow_authoring(
                "flows/review.workflow",
                ["Run three reviews", "Combine their findings"],
                "  Add a security reviewer.  ",
                workflow_touched=True,
            )
        )
        skipped = await tool._record_workflow_authoring(
            "flows/review.workflow",
            ["Run three reviews", "Combine their findings"],
            "Reuse the saved workflow.",
            workflow_touched=False,
        )

    initial = json.loads(await anyio.Path(initial_result["local_path"]).read_text(encoding="utf-8"))
    adjustment = json.loads(await anyio.Path(adjustment_result["local_path"]).read_text(encoding="utf-8"))
    assert skipped is None
    assert initial["flow_key"] == adjustment["flow_key"]
    assert initial["question"] == "Review this repository."
    assert initial["adjustment"] is None
    assert adjustment["question"] is None
    assert adjustment["adjustment"] == "  Add a security reviewer.  "
    assert initial["flow"]["source"] == "workflow version one\n"
    assert adjustment["flow"]["source"] == "workflow version two\n"
    assert initial["flow"]["sha256"] != adjustment["flow"]["sha256"]
    assert str(tmp_path / "workspace") not in json.dumps(adjustment)


@pytest.mark.anyio
async def test_record_rejects_ambiguous_text_and_empty_plan(tmp_path: Path) -> None:
    tool = _load_tool(FEISHU_SKILL)

    with pytest.raises(ValueError, match="exactly one"):
        await tool.workflow_sample_record("flows/a.workflow", ["Plan"], question="Q", adjustment="A")
    with pytest.raises(ValueError, match="non-empty list"):
        await tool.workflow_sample_record("flows/a.workflow", [], question="Q")


@pytest.mark.anyio
async def test_session_captures_generate_only_workflow_without_run_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    flow_path = workspace / "flows" / "generated.workflow"
    flow_path.parent.mkdir(parents=True)
    flow_path.write_text("workflow generated {}\n", encoding="utf-8")
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / "appdata"))

    agent = object.__new__(SessionAgent)
    agent._agent_path = REPO_ROOT / "agents" / "feishu"
    with path_scope(workspace=str(workspace), agent=str(agent._agent_path)):
        await agent._record_authored_workflows(
            {"flows/./generated.workflow"},
            "Generate this workflow only.",
        )

    records = list((tmp_path / "appdata" / "workflow-samples").rglob("*.json"))
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["question"] == "Generate this workflow only."
    assert payload["adjustment"] is None


@pytest.mark.anyio
async def test_run_flow_hook_is_invoked_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_runner()
    observed: list[tuple[str, list[str], str, bool]] = []

    async def recorder(flow_path: str, plan: list[str], message: str, *, workflow_touched: bool) -> str:
        observed.append((flow_path, plan, message, workflow_touched))
        return "saved"

    monkeypatch.setattr(runner, "_record_workflow_authoring", recorder)
    compiled = SimpleNamespace(graph=SimpleNamespace(steps=[]), executor_kinds={})
    with runtime_scope(
        session_id="session",
        workspace=str(REPO_ROOT / "agents" / "feishu"),
        agent=str(REPO_ROOT / "agents" / "feishu"),
        user_message="Author this workflow",
        workflow_touched={"flows/review.workflow"},
    ):
        await runner._record_workflow_sample_if_needed("flows/review.workflow", compiled)

    assert observed == [
        (
            "flows/review.workflow",
            ["Validate the workflow declaration", "Return the declared output artifacts"],
            "Author this workflow",
            True,
        )
    ]


def test_workflow_sample_runtime_is_packaged_in_both_agents() -> None:
    assert not (TOOLS_DIR / "workflow_sample.py").exists()
    assert (FEISHU_SKILL / "workflow_sample.py").read_bytes() == (DESKTOP_SKILL / "workflow_sample.py").read_bytes()
