from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import fusion_flow._atomic_io as atomic_io
import fusion_flow.job_store as job_store
import pytest
from fusion_flow.workflow_execution import create_execution_checkpoint, generate_plan
from fusion_flow.workflow_graph import (
    ArtifactNode,
    ConsumesEdge,
    ProducesEdge,
    StepNode,
    WorkflowGraph,
)

_TOOLS_DIR = Path(__file__).parents[3] / "examples" / "haitun-workspace" / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

run_flow_tool = importlib.import_module("run_flow")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _avoid_sandbox_thread_io(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_sync_now(function, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return function(*args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync_now)
    monkeypatch.setattr(atomic_io, "run_sync_in_worker_thread", run_sync_now)
    monkeypatch.setattr(job_store, "run_sync_in_worker_thread", run_sync_now)


def _graph() -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id="persisted_retry",
        steps=(
            StepNode("step_a", "Step A", "agent"),
            StepNode("step_b", "Step B", "program", depends_on=("step_a",)),
            StepNode("step_c", "Step C", "agent", depends_on=("step_b",)),
            StepNode("step_d", "Step D", "program", depends_on=("step_c",)),
        ),
        artifacts=(
            ArtifactNode("request", is_input=True),
            ArtifactNode("a"),
            ArtifactNode("b"),
            ArtifactNode("c"),
            ArtifactNode("result", is_output=True),
        ),
        edges=(
            ConsumesEdge("request", "step_a"),
            ProducesEdge("step_a", "a"),
            ConsumesEdge("a", "step_b"),
            ProducesEdge("step_b", "b"),
            ConsumesEdge("b", "step_c"),
            ProducesEdge("step_c", "c"),
            ConsumesEdge("c", "step_d"),
            ProducesEdge("step_d", "result"),
        ),
    )


async def _install_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    *,
    execute: Callable[..., Awaitable[dict[str, object]]],
) -> tuple[str, WorkflowGraph]:
    graph = _graph()
    flow_path = "flows/retry/retry.workflow"
    target = workspace / flow_path
    target.parent.mkdir(parents=True)
    target.write_text("stable workflow source", encoding="utf-8")
    compiled = SimpleNamespace(
        graph=graph,
        executor_kinds={"agent": "Agent", "program": "Program"},
    )

    async def materialize_instructions(compiled_workflow, path: str) -> dict[str, str]:
        del compiled_workflow, path
        return {}

    async def run_with_sessions(operation, *, adapter, run_id: str):
        del adapter, run_id
        return await operation()

    monkeypatch.setattr(run_flow_tool, "_workspace_dir", lambda: workspace)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "test-ai-socket")
    monkeypatch.setattr(run_flow_tool, "_compile_workflow_for_run", lambda source, flow_path: compiled)
    monkeypatch.setattr(run_flow_tool, "_materialize_instruction_files", materialize_instructions)
    monkeypatch.setattr(run_flow_tool, "_run_with_agent_sessions", run_with_sessions)
    monkeypatch.setattr(run_flow_tool, "_execute_workflow", execute)
    return flow_path, graph


@pytest.mark.anyio
async def test_run_flow_retry_reuses_persisted_checkpoint_and_original_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_count = 0
    side_effect_count = 0
    observed_calls: list[str] = []

    async def execute(source: str, **kwargs: Any) -> dict[str, object]:
        nonlocal execution_count, side_effect_count
        del source
        execution_count += 1
        checkpoint = kwargs["checkpoint"]
        observer = kwargs["checkpoint_observer"]
        graph = _graph()
        plan = generate_plan(graph)
        assert kwargs["inputs"] == {"request": "original"}
        if not checkpoint.completed_step_ids:
            observed_calls.extend(("step_a", "step_b", "step_c"))
            side_effect_count += 1
            checkpoint = create_execution_checkpoint(
                plan,
                graph,
                values={"request": "original", "a": "a", "b": "b"},
                completed_step_ids=("step_a", "step_b"),
            )
            await observer(checkpoint)
            raise RuntimeError("synthetic step C failure")

        assert checkpoint.completed_step_ids == ("step_a", "step_b")
        observed_calls.extend(("step_c", "step_d"))
        completed = create_execution_checkpoint(
            plan,
            graph,
            values={"request": "original", "a": "a", "b": "b", "c": "c", "result": "done"},
            completed_step_ids=("step_a", "step_b", "step_c", "step_d"),
        )
        await observer(completed)
        return {"result": "done"}

    flow_path, _ = await _install_runtime_fakes(
        monkeypatch,
        tmp_path,
        execute=execute,
    )
    with pytest.raises(RuntimeError, match="synthetic step C failure") as caught:
        await run_flow_tool.run_flow(flow_path, '{"request":"original"}')
    assert any("run_flow_retry" in note for note in caught.value.__notes__)

    store = run_flow_tool._job_store()
    run_files = sorted((tmp_path / ".psi" / "fusion-flow" / "runs").glob("*.json"))
    assert len(run_files) == 1
    run_id = run_files[0].stem
    failed = await store.load(run_id)
    assert failed.status == "failed"
    assert failed.inputs == {"request": "original"}
    assert failed.checkpoint is not None
    assert failed.checkpoint.completed_step_ids == ("step_a", "step_b")

    output_json = await run_flow_tool.run_flow_retry(run_id)

    assert json.loads(output_json) == {"result": "done"}
    assert execution_count == 2
    assert observed_calls == ["step_a", "step_b", "step_c", "step_c", "step_d"]
    assert side_effect_count == 1
    completed = await store.load(run_id)
    assert completed.status == "completed"
    assert completed.outputs == {"result": "done"}


@pytest.mark.anyio
async def test_run_flow_retry_rejects_definition_drift_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_count = 0

    async def execute(source: str, **kwargs: Any) -> dict[str, object]:
        nonlocal execution_count
        del source
        execution_count += 1
        checkpoint = create_execution_checkpoint(
            generate_plan(_graph()),
            _graph(),
            values={"request": "original"},
        )
        await kwargs["checkpoint_observer"](checkpoint)
        raise RuntimeError("synthetic failure")

    flow_path, _ = await _install_runtime_fakes(
        monkeypatch,
        tmp_path,
        execute=execute,
    )
    with pytest.raises(RuntimeError):
        await run_flow_tool.run_flow(flow_path, '{"request":"original"}')
    [run_file] = (tmp_path / ".psi" / "fusion-flow" / "runs").glob("*.json")
    (tmp_path / flow_path).write_text("changed workflow source", encoding="utf-8")

    with pytest.raises(ValueError, match="workflow definition changed"):
        await run_flow_tool.run_flow_retry(run_file.stem)

    assert execution_count == 1
    run = await run_flow_tool._job_store().load(run_file.stem)
    assert run.status == "failed"
    assert run.error == "workflow definition changed after the failed run was checkpointed"


@pytest.mark.anyio
async def test_run_flow_retry_uses_non_blocking_run_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(source: str, **kwargs: Any) -> dict[str, object]:
        del source, kwargs
        raise RuntimeError("synthetic failure")

    flow_path, graph = await _install_runtime_fakes(
        monkeypatch,
        tmp_path,
        execute=execute,
    )
    store = run_flow_tool._job_store()
    run = await store.create(
        flow_path=flow_path,
        definition_digest=run_flow_tool._workflow_definition_digest("stable workflow source", {}),
        inputs={"request": "original"},
        resource_capacities={},
        checkpoint=create_execution_checkpoint(generate_plan(graph), graph, values={"request": "original"}),
    )

    async with store.acquire(run.run_id):
        with pytest.raises(job_store.RunAlreadyActiveError, match="already active"):
            await run_flow_tool.run_flow_retry(run.run_id)
