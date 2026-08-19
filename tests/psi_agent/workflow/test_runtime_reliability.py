from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import anyio
import fusion_flow._atomic_io as atomic_io
import fusion_flow.job_store as job_store
import pytest
from fusion_flow._atomic_io import atomic_write_text
from fusion_flow.artifact_store import ArtifactStore
from fusion_flow.execution import runtime as execution_runtime
from fusion_flow.job_store import JobStore
from fusion_flow.step_timing import (
    StepTiming,
    StepTimingMetadata,
    StepTimingReporter,
    StepTimingStore,
)
from fusion_flow.workflow_execution import (
    DispatchContext,
    ExecutionCheckpoint,
    ExecutionPlanError,
    execute_plan,
    generate_plan,
)
from fusion_flow.workflow_graph import (
    ArtifactNode,
    ConsumesEdge,
    ForeachEdge,
    ProducesEdge,
    StepNode,
    WorkflowGraph,
    WorkflowPolicy,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _avoid_sandbox_thread_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep atomic-file tests deterministic in restricted test environments."""

    async def run_sync_now(function, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return function(*args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync_now)
    monkeypatch.setattr(atomic_io, "run_sync_in_worker_thread", run_sync_now)
    monkeypatch.setattr(job_store, "run_sync_in_worker_thread", run_sync_now)


def _foreach_graph(*, max_attempts: int = 2) -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id="offline_resume_batch",
        steps=(
            StepNode(
                step_id="review",
                name_id="Review synthetic item",
                executor_id="offline_worker",
                max_attempts=max_attempts,
            ),
        ),
        artifacts=(
            ArtifactNode("items", is_input=True),
            ArtifactNode("item", binding_step_id="review"),
            ArtifactNode("reviews", is_output=True),
        ),
        edges=(
            ForeachEdge("items", "review", "item"),
            ProducesEdge("review", "reviews"),
        ),
        policy=WorkflowPolicy(max_concurrency=3),
    )


def _timing_metadata() -> dict[str, StepTimingMetadata]:
    return {
        "review": StepTimingMetadata(
            step_name="Review synthetic item",
            executor_id="offline_worker",
            executor_kind="Agent",
        )
    }


def _side_effect_resume_graph() -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id="side_effect_resume",
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


@pytest.mark.anyio
async def test_resume_skips_completed_side_effect_step_and_continues_downstream() -> None:
    graph = _side_effect_resume_graph()
    plan = generate_plan(graph)
    checkpoints: list[ExecutionCheckpoint] = []
    calls: list[str] = []
    step_c_attempts: list[int] = []
    side_effect_count = 0
    fail_step_c = True

    async def observe(checkpoint: ExecutionCheckpoint) -> None:
        checkpoints.append(checkpoint)

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        nonlocal fail_step_c, side_effect_count
        calls.append(step.step_id)
        if step.step_id == "step_a":
            return {"a": f"a:{inputs['request']}"}
        if step.step_id == "step_b":
            side_effect_count += 1
            return {"b": f"b:{inputs['a']}"}
        if step.step_id == "step_c":
            step_c_attempts.append(context.attempt)
            if fail_step_c:
                fail_step_c = False
                raise RuntimeError("synthetic failure after the side effect")
            return {"c": f"c:{inputs['b']}"}
        assert step.step_id == "step_d"
        return {"result": f"done:{inputs['c']}"}

    with pytest.raises(BaseExceptionGroup):
        await execute_plan(
            plan,
            graph,
            inputs={"request": "input"},
            dispatch=dispatch,
            checkpoint_observer=observe,
        )

    checkpoint = checkpoints[-1]
    assert checkpoint.completed_step_ids == ("step_a", "step_b")
    assert calls == ["step_a", "step_b", "step_c"]
    assert side_effect_count == 1

    with pytest.raises(ExecutionPlanError, match="checkpoint input does not match current input"):
        await execute_plan(
            plan,
            graph,
            inputs={"request": "changed"},
            dispatch=dispatch,
            checkpoint=checkpoint,
        )

    missing_artifact = ExecutionCheckpoint(
        workflow_id=checkpoint.workflow_id,
        plan_digest=checkpoint.plan_digest,
        values={"request": "input", "a": checkpoint.values["a"]},
        completed_step_ids=checkpoint.completed_step_ids,
    )
    with pytest.raises(ExecutionPlanError, match="checkpoint values must match materialized artifacts exactly"):
        await execute_plan(
            plan,
            graph,
            inputs={"request": "input"},
            dispatch=dispatch,
            checkpoint=missing_artifact,
        )

    wrong_plan = ExecutionCheckpoint(
        workflow_id=checkpoint.workflow_id,
        plan_digest="0" * 64,
        values=checkpoint.values,
        completed_step_ids=checkpoint.completed_step_ids,
    )
    with pytest.raises(ExecutionPlanError, match="checkpoint plan digest does not match"):
        await execute_plan(
            plan,
            graph,
            inputs={"request": "input"},
            dispatch=dispatch,
            checkpoint=wrong_plan,
        )

    outputs = await execute_plan(
        plan,
        graph,
        inputs={"request": "input"},
        dispatch=dispatch,
        checkpoint=checkpoint,
        checkpoint_observer=observe,
    )

    assert calls == ["step_a", "step_b", "step_c", "step_c", "step_d"]
    assert step_c_attempts == [1, 1]
    assert side_effect_count == 1
    assert outputs == {"result": "done:c:b:a:input"}


@pytest.mark.anyio
async def test_foreach_timing_preserves_concurrency_order_and_attempts(tmp_path: Path) -> None:
    graph = _foreach_graph()
    reporter = await StepTimingReporter.open(
        anyio.Path(tmp_path),
        run_id="0" * 32,
        workflow_id=graph.workflow_id,
        flow_path="synthetic/offline.workflow",
    )
    active = 0
    peak_active = 0
    completion_order: list[str] = []

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        nonlocal active, peak_active
        assert step.step_id == "review"
        item = str(inputs["item"])
        active += 1
        peak_active = max(peak_active, active)
        try:
            if item == "slow":
                await anyio.sleep(0.08)
            elif item == "retry" and context.attempt == 1:
                await anyio.sleep(0.02)
                raise ValueError("synthetic retry")
            elif item == "retry":
                await anyio.sleep(0.03)
            else:
                await anyio.sleep(0.01)
            completion_order.append(item)
            return {"reviews": f"{item}:{context.attempt}"}
        finally:
            active -= 1

    outputs = await execute_plan(
        generate_plan(graph),
        graph,
        inputs={"items": ["slow", "retry", "fast"]},
        dispatch=dispatch,
        timing_recorder=reporter.record,
        timing_metadata=_timing_metadata(),
    )
    await reporter.finalize(status="completed", error_type=None)

    assert peak_active == 3
    assert completion_order == ["fast", "retry", "slow"]
    assert outputs == {"reviews": ["slow:1", "retry:2", "fast:1"]}

    payload = json.loads((tmp_path / "step-timings.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    [step] = payload["steps"]
    assert step["foreach"] is True
    assert [item["iteration_index"] for item in step["iterations"]] == [0, 1, 2]
    assert [attempt["status"] for attempt in step["iterations"][1]["attempts"]] == ["error", "ok"]
    assert [attempt["attempt"] for attempt in step["iterations"][1]["attempts"]] == [1, 2]


@pytest.mark.anyio
async def test_failed_foreach_sidecar_records_terminal_attempts(tmp_path: Path) -> None:
    graph = _foreach_graph()
    reporter = await StepTimingReporter.open(
        anyio.Path(tmp_path),
        run_id="1" * 32,
        workflow_id=graph.workflow_id,
        flow_path="synthetic/offline.workflow",
    )

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        del step, inputs, context
        raise RuntimeError("synthetic terminal failure")

    with pytest.raises(BaseExceptionGroup) as caught:
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={"items": ["only"]},
            dispatch=dispatch,
            timing_recorder=reporter.record,
            timing_metadata=_timing_metadata(),
        )
    await reporter.finalize(status="failed", error_type=type(caught.value).__name__)

    payload = json.loads((tmp_path / "step-timings.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    [step] = payload["steps"]
    [iteration] = step["iterations"]
    assert step["status"] == "error"
    assert iteration["status"] == "error"
    assert [attempt["attempt"] for attempt in iteration["attempts"]] == [1, 2]
    assert [attempt["error_type"] for attempt in iteration["attempts"]] == ["RuntimeError", "RuntimeError"]


@pytest.mark.anyio
async def test_resume_merges_running_timing_sidecar_and_reuses_successes(tmp_path: Path) -> None:
    graph = _foreach_graph()
    plan = generate_plan(graph)
    inputs = {"items": ["cached-a", "resume-me", "cached-b"]}
    checkpoints: list[ExecutionCheckpoint] = []
    first_reporter = await StepTimingReporter.open(
        anyio.Path(tmp_path),
        run_id="2" * 32,
        workflow_id=graph.workflow_id,
        flow_path="synthetic/offline.workflow",
    )

    async def observe(checkpoint: ExecutionCheckpoint) -> None:
        checkpoints.append(checkpoint)
        await first_reporter.persist()

    async def first_dispatch(
        step: StepNode,
        step_inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        del step, context
        item = str(step_inputs["item"])
        if item == "resume-me":
            raise LookupError("synthetic interruption boundary")
        return {"reviews": f"first:{item}"}

    with pytest.raises(BaseExceptionGroup):
        await execute_plan(
            plan,
            graph,
            inputs=inputs,
            dispatch=first_dispatch,
            checkpoint_observer=observe,
            timing_recorder=first_reporter.record,
            timing_metadata=_timing_metadata(),
        )
    await first_reporter.persist()
    checkpoint = checkpoints[-1]
    assert checkpoint.foreach_iterations[0].outputs == {"reviews": "first:cached-a"}
    assert checkpoint.foreach_iterations[1].error is not None
    assert checkpoint.foreach_iterations[2].outputs == {"reviews": "first:cached-b"}

    resumed_calls: list[str] = []
    resumed_reporter = await StepTimingReporter.open(
        anyio.Path(tmp_path),
        run_id="2" * 32,
        workflow_id=graph.workflow_id,
        flow_path="synthetic/offline.workflow",
    )

    async def resumed_dispatch(
        step: StepNode,
        step_inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        del step, context
        item = str(step_inputs["item"])
        resumed_calls.append(item)
        return {"reviews": f"resumed:{item}"}

    outputs = await execute_plan(
        plan,
        graph,
        inputs=inputs,
        dispatch=resumed_dispatch,
        checkpoint=checkpoint,
        checkpoint_observer=lambda value: anyio.lowlevel.checkpoint(),
        timing_recorder=resumed_reporter.record,
        timing_metadata=_timing_metadata(),
    )
    await resumed_reporter.finalize(status="completed", error_type=None)

    assert resumed_calls == ["resume-me"]
    assert outputs == {"reviews": ["first:cached-a", "resumed:resume-me", "first:cached-b"]}
    payload = json.loads((tmp_path / "step-timings.json").read_text(encoding="utf-8"))
    [step] = payload["steps"]
    assert payload["status"] == "completed"
    assert [item["iteration_index"] for item in step["iterations"]] == [0, 1, 2]


@pytest.mark.anyio
async def test_explicit_retry_reopens_terminal_timing_sidecar(tmp_path: Path) -> None:
    graph = _foreach_graph()
    run_id = "3" * 32
    first_reporter = await StepTimingReporter.open(
        anyio.Path(tmp_path),
        run_id=run_id,
        workflow_id=graph.workflow_id,
        flow_path="synthetic/offline.workflow",
    )
    first_reporter.record(
        StepTiming(
            step_id="review",
            step_name="Review synthetic item",
            executor_id="offline_worker",
            executor_kind="Agent",
            foreach=True,
            started_at="2026-08-19T00:00:00Z",
            finished_at="2026-08-19T00:00:01Z",
            duration_ms=1000,
            status="error",
            error_type="RuntimeError",
            iterations=(),
        )
    )
    await first_reporter.finalize(status="failed", error_type="RuntimeError")

    with pytest.raises(ValueError, match="resumable step timing report must have running status"):
        await StepTimingStore.open(
            anyio.Path(tmp_path),
            run_id=run_id,
            workflow_id=graph.workflow_id,
            flow_path="synthetic/offline.workflow",
        )

    resumed_reporter = await StepTimingReporter.open(
        anyio.Path(tmp_path),
        run_id=run_id,
        workflow_id=graph.workflow_id,
        flow_path="synthetic/offline.workflow",
        resume_terminal=True,
    )
    reopened = json.loads((tmp_path / "step-timings.json").read_text(encoding="utf-8"))
    assert reopened["status"] == "running"
    assert reopened["error_type"] is None
    assert [step["step_id"] for step in reopened["steps"]] == ["review"]

    resumed_reporter.record(
        StepTiming(
            step_id="review",
            step_name="Review synthetic item",
            executor_id="offline_worker",
            executor_kind="Agent",
            foreach=True,
            started_at="2026-08-19T00:00:02Z",
            finished_at="2026-08-19T00:00:03Z",
            duration_ms=1000,
            status="ok",
            iterations=(),
        )
    )
    await resumed_reporter.finalize(status="completed", error_type=None)

    completed = json.loads((tmp_path / "step-timings.json").read_text(encoding="utf-8"))
    assert completed["status"] == "completed"
    assert completed["steps"][0]["status"] == "ok"
    assert completed["steps"][0]["duration_ms"] == 2000


@pytest.mark.anyio
async def test_shared_atomic_publication_preserves_target_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ArtifactStore is not None
    assert JobStore is not None
    assert StepTimingStore is not None
    assert execution_runtime._atomic_write_text is not None

    target = tmp_path / "state.json"
    target.write_text("old\n", encoding="utf-8")
    real_replace = os.replace

    def fail_publish(source: object, destination: object) -> None:
        if Path(str(destination)) == target:
            raise OSError("synthetic replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publish)
    with pytest.raises(OSError, match="synthetic replace failure"):
        await atomic_write_text(anyio.Path(target), "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    names = [path.name async for path in anyio.Path(tmp_path).iterdir()]
    assert names == ["state.json"]
