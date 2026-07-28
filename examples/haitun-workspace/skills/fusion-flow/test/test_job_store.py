from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import replace
from typing import Any

import anyio
import fusion_flow.job_store as job_store_module
import pytest
from fusion_flow.job_store import (
    HumanRequestSpec,
    HumanWorkflowRun,
    InvalidRunStateError,
    JobStore,
    JobStoreError,
    RunAlreadyActiveError,
)

from psi_agent.workflow_execution import ExecutionCheckpoint

_WORKFLOW_ID = "review_workflow"
_PLAN_DIGEST = "c" * 64


@pytest.mark.anyio
async def test_job_store_round_trips_waiting_human_state(tmp_path: Any) -> None:
    store = JobStore(tmp_path / "runs")
    run = await store.create(
        flow_path="flows/review.workflow",
        flow_source="const review: Workflow;",
        inputs={"proposal": {"version": 2}},
        resource_capacities={"gpu": ("gpu-a", "gpu-b"), "browser": 1},
    )

    assert re.fullmatch(r"[0-9a-f]{32}", run.run_id)
    assert run.status == "running"
    checkpoint = ExecutionCheckpoint(
        workflow_id=_WORKFLOW_ID,
        plan_digest=_PLAN_DIGEST,
        values={"proposal": {"version": 2}, "draft": "ready"},
        completed_step_ids=("draft_step",),
    )
    request = HumanRequestSpec.create(
        step_id="review_step",
        question="Approve the proposal or provide edits?",
        output_artifact_ids=("review",),
        options=("Approve", "Request changes"),
        recommended=1,
    )
    waiting = replace(
        run,
        status="waiting_for_human",
        checkpoint=checkpoint,
        prepared_request=request,
    )

    async with store.acquire(run.run_id) as lease:
        await lease.save(waiting)

    loaded = await store.load(run.run_id)
    assert loaded == waiting
    assert loaded.resource_capacities == {
        "browser": 1,
        "gpu": ("gpu-a", "gpu-b"),
    }

    state_path = anyio.Path(tmp_path / "runs" / f"{run.run_id}.json")
    payload = json.loads(await state_path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["prepared_request"]["request_id"] == request.request_id
    assert payload["checkpoint"] == {
        "workflow_id": _WORKFLOW_ID,
        "plan_digest": _PLAN_DIGEST,
        "values": {"draft": "ready", "proposal": {"version": 2}},
        "completed_step_ids": ["draft_step"],
        "completed_selection_ids": [],
    }


@pytest.mark.anyio
async def test_job_store_accepts_a_precomputed_workflow_definition_digest(tmp_path: Any) -> None:
    store = JobStore(tmp_path / "runs")
    definition_digest = "d" * 64

    run = await store.create(
        flow_path="flows/review.workflow",
        flow_source="workflow source",
        definition_digest=definition_digest,
        inputs={},
    )

    assert run.flow_source_digest == definition_digest
    assert (await store.load(run.run_id)).flow_source_digest == definition_digest


@pytest.mark.anyio
async def test_job_store_persists_response_and_completed_outputs(tmp_path: Any) -> None:
    store = JobStore(tmp_path)
    run = await store.create(
        flow_path="review.workflow",
        flow_source="workflow",
        inputs={"request": "review"},
        checkpoint=ExecutionCheckpoint(
            workflow_id=_WORKFLOW_ID,
            plan_digest=_PLAN_DIGEST,
            values={"request": "review"},
        ),
    )
    request = HumanRequestSpec.create(
        step_id="review",
        question="What should change?",
        output_artifact_ids=("feedback",),
    )
    waiting = replace(
        run,
        status="waiting_for_human",
        prepared_request=request,
    )
    async with store.acquire(run.run_id) as lease:
        await lease.save(waiting)
        resumed = replace(
            waiting,
            status="running",
            prepared_request=None,
            human_responses={request.request_id: "Tighten the conclusion."},
        )
        await lease.save(resumed)
        completed = replace(
            resumed,
            status="completed",
            outputs={"result": {"accepted": True}},
        )
        await lease.save(completed)

    loaded = await store.load(run.run_id)
    assert loaded.status == "completed"
    assert loaded.human_responses == {request.request_id: "Tighten the conclusion."}
    assert loaded.outputs == {"result": {"accepted": True}}


@pytest.mark.anyio
async def test_acquire_rejects_duplicate_active_run_and_releases_after_error(
    tmp_path: Any,
) -> None:
    store = JobStore(tmp_path)
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )

    with pytest.raises(RuntimeError, match="stop"):
        async with store.acquire(run.run_id) as lease:
            assert await lease.load() == run
            with pytest.raises(RunAlreadyActiveError, match="already active"):
                async with store.acquire(run.run_id):
                    pytest.fail("duplicate lease was granted")
            raise RuntimeError("stop")

    async with store.acquire(run.run_id) as lease:
        assert await lease.load() == run

    with pytest.raises(JobStoreError, match="no longer active"):
        await lease.load()


@pytest.mark.anyio
async def test_process_guard_rejects_duplicate_when_os_backend_is_permissive(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "runs"
    first_store = JobStore(root)
    second_store = JobStore(os.path.join(str(root), "."))
    run = await first_store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )
    backend_calls = 0

    def permissive_backend(_path: str) -> io.BytesIO:
        nonlocal backend_calls
        backend_calls += 1
        return io.BytesIO()

    monkeypatch.setattr(
        job_store_module,
        "_try_open_locked_file",
        permissive_backend,
    )

    async with first_store.acquire(run.run_id):
        assert backend_calls == 1
        with pytest.raises(RunAlreadyActiveError, match="already active"):
            async with second_store.acquire(run.run_id):
                pytest.fail("process-local duplicate lease was granted")
        assert backend_calls == 1

    async with second_store.acquire(run.run_id) as lease:
        assert await lease.load() == run
    assert backend_calls == 2


@pytest.mark.anyio
async def test_acquire_releases_lock_when_holder_is_cancelled(tmp_path: Any) -> None:
    store = JobStore(tmp_path)
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )
    acquired = anyio.Event()

    async def hold_lease() -> None:
        async with store.acquire(run.run_id):
            acquired.set()
            await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(hold_lease)
        await acquired.wait()
        task_group.cancel_scope.cancel()

    async with store.acquire(run.run_id) as lease:
        assert await lease.load() == run


@pytest.mark.anyio
async def test_advisory_lock_is_released_after_holder_process_crashes(
    tmp_path: Any,
) -> None:
    store = JobStore(tmp_path / "runs")
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )
    ready_path = anyio.Path(tmp_path / "child-holds-lock")
    skill_root = os.path.dirname(os.path.dirname(__file__))
    child_source = """
import sys

import anyio

from fusion_flow.job_store import JobStore


async def main() -> None:
    store = JobStore(sys.argv[1])
    async with store.acquire(sys.argv[2]):
        await anyio.Path(sys.argv[3]).write_text("locked", encoding="utf-8")
        await anyio.sleep_forever()


anyio.run(main)
"""
    process = await anyio.open_process(
        [
            sys.executable,
            "-c",
            child_source,
            str(tmp_path / "runs"),
            run.run_id,
            str(ready_path),
        ],
        cwd=skill_root,
    )
    try:
        with anyio.fail_after(10):
            while not await ready_path.exists():
                if process.returncode is not None:
                    pytest.fail(f"lock-holder process exited with {process.returncode}")
                await anyio.sleep(0.01)

        with pytest.raises(RunAlreadyActiveError, match="already active"):
            async with store.acquire(run.run_id):
                pytest.fail("lease held by another process was granted")

        process.kill()
        await process.wait()

        async with store.acquire(run.run_id) as lease:
            assert await lease.load() == run
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.anyio
async def test_acquire_ignores_pre_advisory_lock_directory(tmp_path: Any) -> None:
    store = JobStore(tmp_path)
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )
    legacy_lock_dir = anyio.Path(tmp_path / "locks" / f"{run.run_id}.lock")
    await legacy_lock_dir.mkdir()

    async with store.acquire(run.run_id) as lease:
        assert await lease.load() == run

    assert await legacy_lock_dir.is_dir()


@pytest.mark.anyio
async def test_load_strictly_rejects_unknown_fields_and_filename_mismatch(
    tmp_path: Any,
) -> None:
    store = JobStore(tmp_path)
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )
    state_path = anyio.Path(tmp_path / f"{run.run_id}.json")
    payload = json.loads(await state_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    await state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidRunStateError, match="unknown=\\['unexpected'\\]"):
        await store.load(run.run_id)


@pytest.mark.anyio
async def test_load_rejects_legacy_unbound_checkpoint_schema(tmp_path: Any) -> None:
    store = JobStore(tmp_path)
    checkpoint = ExecutionCheckpoint(
        workflow_id=_WORKFLOW_ID,
        plan_digest=_PLAN_DIGEST,
        values={},
    )
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
        checkpoint=checkpoint,
    )
    state_path = anyio.Path(tmp_path / f"{run.run_id}.json")
    payload = json.loads(await state_path.read_text(encoding="utf-8"))
    payload["version"] = 1
    del payload["checkpoint"]["workflow_id"]
    del payload["checkpoint"]["plan_digest"]
    await state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidRunStateError, match="unsupported run state version"):
        await store.load(run.run_id)


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_version", [2.0, True])
async def test_load_rejects_non_integer_state_version(
    invalid_version: object,
    tmp_path: Any,
) -> None:
    store = JobStore(tmp_path)
    run = await store.create(
        flow_path="flow.workflow",
        flow_source="workflow",
        inputs={},
    )
    state_path = anyio.Path(tmp_path / f"{run.run_id}.json")
    payload = json.loads(await state_path.read_text(encoding="utf-8"))
    payload["version"] = invalid_version
    await state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidRunStateError, match="unsupported run state version"):
        await store.load(run.run_id)


@pytest.mark.anyio
async def test_load_rejects_path_traversal_identifier(tmp_path: Any) -> None:
    store = JobStore(tmp_path)

    with pytest.raises(ValueError, match="32 lowercase hexadecimal"):
        await store.load("../outside")


def test_run_state_rejects_non_json_values_and_invalid_clarify_spec() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        HumanWorkflowRun(
            run_id="a" * 32,
            status="running",
            flow_path="flow.workflow",
            flow_source_digest="b" * 64,
            inputs={"bad": float("nan")},
            resource_capacities={},
        )

    with pytest.raises(ValueError, match="at most four"):
        HumanRequestSpec.create(
            step_id="review",
            question="Choose",
            output_artifact_ids=("answer",),
            options=("1", "2", "3", "4", "5"),
        )
