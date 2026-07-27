from __future__ import annotations

import json
import os
import sys
from typing import cast

import anyio
import pytest
from anyio.lowlevel import checkpoint
from anyio.to_thread import run_sync as run_sync_in_worker_thread

from psi_agent.fusion_flow import runtime as runtime_module
from psi_agent.fusion_flow.flow import flow
from psi_agent.fusion_flow.model import (
    AgentConfig,
    AgentInvocation,
    ExecutionTrace,
    TraceStatus,
)
from psi_agent.fusion_flow.runtime import RunContext, gc_runs, run


def test_generated_run_id_uses_six_base36_characters(monkeypatch) -> None:
    characters = iter("0az9by")
    alphabets: list[str] = []

    def pick(alphabet: str) -> str:
        alphabets.append(alphabet)
        return next(characters)

    monkeypatch.setattr(runtime_module, "choice", pick)

    assert runtime_module._make_run_id().endswith("-0az9by")
    assert alphabets == ["0123456789abcdefghijklmnopqrstuvwxyz"] * 6


@pytest.mark.anyio
async def test_run_persists_inputs_bindings_and_final_metadata(tmp_path) -> None:
    async def program(ctx: RunContext) -> None:
        value = await ctx.input("topic", "default")
        await ctx.save("answer", value.upper())

    result = await run(
        program,
        runs_dir=tmp_path,
        inputs={"topic": "python"},
        run_id="run-ok",
    )

    run_dir = anyio.Path(result.run_dir)
    assert result.status == "ok"
    assert await anyio.Path(run_dir, "input", "topic.md").read_text() == "python"
    assert await anyio.Path(run_dir, "bindings", "answer.md").read_text() == "PYTHON"

    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    assert meta["status"] == "ok"
    assert meta["run_id"] == "run-ok"
    assert meta["tokens"] == {
        "calls": 0,
        "input": 0,
        "internal": {"calls": 0, "input": 0, "output": 0},
        "output": 0,
        "user": {"calls": 0, "input": 0, "output": 0},
    }
    assert meta["session_calls"] == {}
    assert meta["evaluator_calls"] == {}
    assert meta["service_calls"] == {}
    assert meta["total_tokens"] == {"input": 0, "output": 0}
    assert meta["llm_calls"] == 0
    assert meta["user_tokens"] == {"input": 0, "output": 0}
    assert meta["user_llm_calls"] == 0
    assert meta["evaluator_tokens"] == {"input": 0, "output": 0}
    assert meta["evaluator_llm_calls"] == 0
    assert graph["root"]["status"] == "ok"
    assert not [path async for path in run_dir.iterdir() if ".tmp-" in path.name]


@pytest.mark.anyio
async def test_progress_records_paired_start_and_end_events(tmp_path) -> None:
    async def program(ctx: RunContext) -> None:
        await ctx.input("topic", "python")
        await ctx.save("answer", "done")

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="progress-events",
        throw_on_error=True,
    )

    lines = (await anyio.Path(result.run_dir, "progress.jsonl").read_text()).splitlines()
    events = [json.loads(line) for line in lines]

    assert [event["event"] for event in events] == [
        "node_start",
        "node_end",
    ]
    for start, end in zip(events[::2], events[1::2], strict=True):
        assert set(start) == {"ts", "event", "id", "type", "label"}
        assert set(end) == {
            "ts",
            "event",
            "id",
            "type",
            "label",
            "status",
            "durationMs",
        }
        assert start["id"] == end["id"]
        assert start["type"] == end["type"]
        assert start["label"] == end["label"]
        assert end["status"] == "ok"

    graph = json.loads(await anyio.Path(result.run_dir, "execution-graph.json").read_text())
    metadata = json.loads(
        await anyio.Path(
            result.run_dir,
            "bindings",
            "answer.meta.json",
        ).read_text()
    )
    assert [child["kind"] for child in graph["root"]["children"]] == ["input"]
    assert metadata["source_node"] == graph["root"]["trace_id"]


@pytest.mark.anyio
async def test_progress_append_closes_before_propagating_cancellation(
    tmp_path,
    monkeypatch,
) -> None:
    active_scope: anyio.CancelScope | None = None

    class CancelAfterWriteStream:
        def __init__(self) -> None:
            self.closed = False
            self.lines: list[str] = []

        async def __aenter__(self) -> CancelAfterWriteStream:
            return self

        async def __aexit__(self, *_: object) -> None:
            await checkpoint()
            self.closed = True

        async def write(self, value: str) -> None:
            self.lines.append(value)
            assert active_scope is not None
            active_scope.cancel()
            await checkpoint()

    stream = CancelAfterWriteStream()

    async def open_progress(
        path: anyio.Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> CancelAfterWriteStream:
        assert path.name == "progress.jsonl"
        assert mode == "a"
        assert buffering == -1
        assert encoding == "utf-8"
        assert errors is None
        assert newline is None
        return stream

    monkeypatch.setattr(anyio.Path, "open", open_progress)

    trace = ExecutionTrace(
        trace_id="input-progress",
        kind="input",
        label="topic",
        started_at="2026-07-26T00:00:00Z",
    )
    context = RunContext(
        run_id="progress-cancel",
        run_dir=anyio.Path(tmp_path),
        inputs={},
        runner=None,
        root_trace=trace,
        resumed=False,
        resume_bindings={},
    )
    returned_normally = False

    with anyio.CancelScope() as scope:
        active_scope = scope
        await context._record_progress(trace, "node_start")
        returned_normally = True

    assert not returned_normally
    assert stream.closed
    assert len(stream.lines) == 1
    assert trace.trace_id in context._progress_started


@pytest.mark.anyio
async def test_trace_retries_failed_start_before_successful_end(
    tmp_path,
    monkeypatch,
) -> None:
    original_record_progress = RunContext._record_progress
    start_attempts = 0

    async def fail_first_start(
        context: RunContext,
        trace,
        event: str,
    ) -> None:
        nonlocal start_attempts
        if event == "node_start":
            start_attempts += 1
            if start_attempts == 1:
                raise OSError("transient progress failure")
        await original_record_progress(context, trace, event)

    monkeypatch.setattr(RunContext, "_record_progress", fail_first_start)

    async def program(ctx: RunContext) -> None:
        await ctx.input("topic", "python")

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="retry-progress-start",
        throw_on_error=True,
    )
    events = [
        json.loads(line)
        for line in (
            await anyio.Path(
                result.run_dir,
                "progress.jsonl",
            ).read_text()
        ).splitlines()
    ]

    assert start_attempts == 2
    assert [event["event"] for event in events] == ["node_start", "node_end"]
    assert events[-1]["status"] == "ok"


@pytest.mark.anyio
async def test_trace_cancelled_while_retrying_failed_start_is_terminalized(
    tmp_path,
    monkeypatch,
) -> None:
    original_record_progress = RunContext._record_progress
    active_scope: anyio.CancelScope | None = None
    start_attempts = 0

    async def fail_then_cancel_start(
        context: RunContext,
        trace: ExecutionTrace,
        event: str,
    ) -> None:
        nonlocal start_attempts
        if event == "node_start":
            start_attempts += 1
            if start_attempts == 1:
                raise OSError("transient progress failure")
            if start_attempts == 2:
                assert active_scope is not None
                active_scope.cancel()
                await checkpoint()
        await original_record_progress(context, trace, event)

    monkeypatch.setattr(RunContext, "_record_progress", fail_then_cancel_start)

    async def program(ctx: RunContext) -> None:
        nonlocal active_scope
        with anyio.CancelScope() as scope:
            active_scope = scope
            async with ctx._trace("block", "retry-cancelled-start"):
                pass

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="cancelled-retry-progress-start",
        throw_on_error=True,
    )
    progress_path = anyio.Path(result.run_dir, "progress.jsonl")
    progress = (
        [
            json.loads(line)
            for line in (await progress_path.read_text()).splitlines()
            if '"label":"retry-cancelled-start"' in line
        ]
        if await progress_path.exists()
        else []
    )
    graph = json.loads(await anyio.Path(result.run_dir, "execution-graph.json").read_text())

    assert start_attempts == 3
    assert [event["event"] for event in progress] == ["node_start", "node_end"]
    assert progress[-1]["status"] == "cancelled"
    assert graph["root"]["children"][0]["status"] == "cancelled"


@pytest.mark.anyio
async def test_input_and_output_names_are_normalized_in_artifacts(tmp_path) -> None:
    input_name = "cafe\u0301"
    output_name = "re\u0301sume\u0301"

    async def program(ctx: RunContext) -> None:
        assert await ctx.input(input_name, "value") == "value"
        await ctx.save(output_name, "done")

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="normalized-artifacts",
        throw_on_error=True,
    )

    graph = json.loads(await anyio.Path(result.run_dir, "execution-graph.json").read_text(encoding="utf-8"))
    metadata = json.loads(
        await anyio.Path(
            result.run_dir,
            "bindings",
            "r\u00e9sum\u00e9.meta.json",
        ).read_text(encoding="utf-8")
    )

    assert graph["root"]["children"][0]["label"] == "caf\u00e9"
    assert metadata["name"] == "r\u00e9sum\u00e9"
    assert await anyio.Path(result.run_dir, "input", "caf\u00e9.md").exists()
    assert await anyio.Path(
        result.run_dir,
        "bindings",
        "r\u00e9sum\u00e9.md",
    ).exists()


@pytest.mark.anyio
async def test_run_context_exposes_package_flow(tmp_path) -> None:
    async def program(ctx: RunContext) -> None:
        assert ctx.flow is flow

    await run(
        program,
        runs_dir=tmp_path,
        run_id="context-flow",
        throw_on_error=True,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("filename", ["workflow.py", "workflow"])
async def test_run_snapshots_sys_argv_entry_script_by_default(
    tmp_path,
    monkeypatch,
    filename: str,
) -> None:
    source = anyio.Path(tmp_path, filename)
    content = "async def workflow(ctx):\n    return None\n"
    await source.write_text(content)
    monkeypatch.setattr(sys, "argv", [str(source)])

    async def program(_: RunContext) -> None:
        return None

    result = await run(
        program,
        runs_dir=anyio.Path(tmp_path, "runs"),
        run_id="default-snapshot",
        throw_on_error=True,
    )

    assert await anyio.Path(result.run_dir, "program.py").read_text() == content
    meta = json.loads(await anyio.Path(result.run_dir, "meta.json").read_text())
    assert meta["program_snapshot"] == str(source)


@pytest.mark.anyio
async def test_run_snapshots_explicit_program_byte_for_byte(tmp_path) -> None:
    source = anyio.Path(tmp_path, "latin1_program.py")
    content = b"# coding: latin-1\nmessage = 'caf\xe9'\n"
    await source.write_bytes(content)

    async def program(_: RunContext) -> None:
        return

    result = await run(
        program,
        runs_dir=anyio.Path(tmp_path, "runs"),
        run_id="byte-snapshot",
        program_path=source,
        throw_on_error=True,
    )

    assert await anyio.Path(result.run_dir, "program.py").read_bytes() == content


@pytest.mark.anyio
async def test_run_records_normal_errors_without_reraising_by_default(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        raise ValueError("broken")

    result = await run(program, runs_dir=tmp_path, run_id="run-error")

    assert result.status == "error"
    meta = json.loads(await anyio.Path(result.run_dir, "meta.json").read_text())
    assert meta["status"] == "error"
    assert meta["error"] == "broken"


@pytest.mark.anyio
async def test_run_reraises_only_after_persisting_when_requested(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        raise LookupError("missing")

    with pytest.raises(LookupError, match="missing"):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="run-raise",
            throw_on_error=True,
        )

    meta = json.loads(await anyio.Path(tmp_path, "run-raise", "meta.json").read_text())
    assert meta["status"] == "error"
    assert meta["error"] == "missing"


@pytest.mark.anyio
async def test_run_propagates_cancellation_after_shielded_persistence(tmp_path) -> None:
    started = anyio.Event()

    async def program(_: RunContext) -> None:
        started.set()
        await anyio.sleep_forever()

    async def invoke() -> None:
        await run(program, runs_dir=tmp_path, run_id="run-cancelled")

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await started.wait()
        task_group.cancel_scope.cancel()

    meta = json.loads(await anyio.Path(tmp_path, "run-cancelled", "meta.json").read_text())
    graph = json.loads(
        await anyio.Path(
            tmp_path,
            "run-cancelled",
            "execution-graph.json",
        ).read_text()
    )
    assert meta["status"] == "cancelled"
    assert graph["root"]["status"] == "cancelled"


@pytest.mark.anyio
async def test_run_propagates_cancellation_arriving_during_final_persistence(
    tmp_path,
    monkeypatch,
) -> None:
    persistence_started = anyio.Event()
    release_persistence = anyio.Event()
    returned_normally = False
    original_persist_final_state = runtime_module._persist_final_state

    async def block_final_persistence(
        context: RunContext,
        *,
        status: TraceStatus,
        started_at: str,
        started: float,
        error: BaseException | None,
        resume_from_run_id: str | None,
        program_snapshot: str | None,
    ) -> None:
        persistence_started.set()
        await release_persistence.wait()
        await original_persist_final_state(
            context,
            status=status,
            started_at=started_at,
            started=started,
            error=error,
            resume_from_run_id=resume_from_run_id,
            program_snapshot=program_snapshot,
        )

    monkeypatch.setattr(
        runtime_module,
        "_persist_final_state",
        block_final_persistence,
    )

    async def program(_: RunContext) -> None:
        return

    async def invoke() -> None:
        nonlocal returned_normally
        await run(
            program,
            runs_dir=tmp_path,
            run_id="cancelled-during-final-persistence",
        )
        returned_normally = True

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await persistence_started.wait()
        task_group.cancel_scope.cancel()
        release_persistence.set()

    run_dir = anyio.Path(tmp_path, "cancelled-during-final-persistence")
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())

    assert not returned_normally
    assert meta["status"] == "ok"
    assert graph["root"]["status"] == "ok"


@pytest.mark.anyio
async def test_cancelled_run_logs_final_persistence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    started = anyio.Event()
    persistence_attempted = anyio.Event()
    errors: list[str] = []

    async def fail_final_persistence(*_: object, **__: object) -> None:
        persistence_attempted.set()
        raise OSError("disk failed")

    monkeypatch.setattr(runtime_module, "_persist_final_state", fail_final_persistence)
    monkeypatch.setattr(runtime_module.logger, "error", errors.append)

    async def program(_: RunContext) -> None:
        started.set()
        await anyio.sleep_forever()

    async def invoke() -> None:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="cancelled-persistence-error",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await started.wait()
        task_group.cancel_scope.cancel()

    assert persistence_attempted.is_set()
    assert any("Failed to persist cancelled FusionFlow run" in message for message in errors)
    assert any("disk failed" in message for message in errors)


@pytest.mark.anyio
async def test_late_cancellation_logs_final_persistence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    persistence_started = anyio.Event()
    release_persistence = anyio.Event()
    errors: list[str] = []
    returned_normally = False

    async def block_then_fail(*_: object, **__: object) -> None:
        persistence_started.set()
        await release_persistence.wait()
        raise OSError("disk failed")

    monkeypatch.setattr(runtime_module, "_persist_final_state", block_then_fail)
    monkeypatch.setattr(runtime_module.logger, "error", errors.append)

    async def program(_: RunContext) -> None:
        return

    async def invoke() -> None:
        nonlocal returned_normally
        await run(
            program,
            runs_dir=tmp_path,
            run_id="late-cancel-persistence-error",
        )
        returned_normally = True

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await persistence_started.wait()
        task_group.cancel_scope.cancel()
        release_persistence.set()

    assert not returned_normally
    assert any("Failed to persist cancelled FusionFlow run" in message for message in errors)
    assert any("disk failed" in message for message in errors)


@pytest.mark.anyio
async def test_cancellation_after_terminal_progress_does_not_duplicate_events(
    tmp_path,
    monkeypatch,
) -> None:
    original_record_progress = RunContext._record_progress
    active_scope: anyio.CancelScope | None = None
    cancelled = False

    async def cancel_after_terminal_write(
        context: RunContext,
        trace: ExecutionTrace,
        event: str,
    ) -> None:
        nonlocal cancelled
        await original_record_progress(context, trace, event)
        if event == "node_end" and trace.label == "finished" and not cancelled:
            cancelled = True
            assert active_scope is not None
            active_scope.cancel()
            await checkpoint()

    monkeypatch.setattr(
        RunContext,
        "_record_progress",
        cancel_after_terminal_write,
    )

    async def program(ctx: RunContext) -> None:
        nonlocal active_scope
        with anyio.CancelScope() as scope:
            active_scope = scope
            async with ctx._trace("block", "finished"):
                pass

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="cancel-after-terminal-progress",
        throw_on_error=True,
    )
    progress = [
        json.loads(line)
        for line in (await anyio.Path(result.run_dir, "progress.jsonl").read_text()).splitlines()
        if '"label":"finished"' in line
    ]
    graph = json.loads(await anyio.Path(result.run_dir, "execution-graph.json").read_text())

    assert [event["event"] for event in progress] == ["node_start", "node_end"]
    assert progress[-1]["status"] == "ok"
    assert graph["root"]["children"][0]["status"] == "ok"


@pytest.mark.anyio
async def test_trace_cancelled_after_start_is_terminalized(
    tmp_path,
    monkeypatch,
) -> None:
    start_written = anyio.Event()
    original_record_progress = RunContext._record_progress

    async def block_after_start(
        context: RunContext,
        trace,
        event: str,
    ) -> None:
        await original_record_progress(context, trace, event)
        if event == "node_start":
            start_written.set()
            await anyio.sleep_forever()

    monkeypatch.setattr(RunContext, "_record_progress", block_after_start)

    async def program(ctx: RunContext) -> None:
        await ctx.input("topic", "python")

    async def invoke() -> None:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="cancelled-trace-start",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await start_written.wait()
        task_group.cancel_scope.cancel()

    run_dir = anyio.Path(tmp_path, "cancelled-trace-start")
    events = [json.loads(line) for line in (await anyio.Path(run_dir, "progress.jsonl").read_text()).splitlines()]
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())

    assert [event["event"] for event in events] == ["node_start", "node_end"]
    assert events[-1]["status"] == "cancelled"
    assert graph["root"]["children"][0]["status"] == "cancelled"


@pytest.mark.anyio
async def test_trace_cancelled_while_writing_end_is_terminalized(
    tmp_path,
    monkeypatch,
) -> None:
    end_started = anyio.Event()
    original_record_progress = RunContext._record_progress
    blocked = False

    async def block_first_end(
        context: RunContext,
        trace,
        event: str,
    ) -> None:
        nonlocal blocked
        if event == "node_end" and not blocked:
            blocked = True
            end_started.set()
            await anyio.sleep_forever()
        await original_record_progress(context, trace, event)

    monkeypatch.setattr(RunContext, "_record_progress", block_first_end)

    async def program(ctx: RunContext) -> None:
        await ctx.input("topic", "python")

    async def invoke() -> None:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="cancelled-trace-end",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await end_started.wait()
        task_group.cancel_scope.cancel()

    run_dir = anyio.Path(tmp_path, "cancelled-trace-end")
    events = [json.loads(line) for line in (await anyio.Path(run_dir, "progress.jsonl").read_text()).splitlines()]
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())

    assert [event["event"] for event in events] == ["node_start", "node_end"]
    assert events[-1]["status"] == "cancelled"
    assert graph["root"]["children"][0]["status"] == "cancelled"


@pytest.mark.anyio
async def test_trace_cancelled_while_attaching_child_is_terminalized(
    tmp_path,
    monkeypatch,
) -> None:
    attached = anyio.Event()
    original_append_child = RunContext._append_child

    async def block_after_append(context: RunContext, parent, child) -> None:
        await original_append_child(context, parent, child)
        attached.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(RunContext, "_append_child", block_after_append)

    async def program(ctx: RunContext) -> None:
        await ctx.input("topic", "python")

    async def invoke() -> None:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="cancelled-trace-attachment",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await attached.wait()
        task_group.cancel_scope.cancel()

    run_dir = anyio.Path(tmp_path, "cancelled-trace-attachment")
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    events = [json.loads(line) for line in (await anyio.Path(run_dir, "progress.jsonl").read_text()).splitlines()]

    assert graph["root"]["children"][0]["status"] == "cancelled"
    assert [event["event"] for event in events] == ["node_start", "node_end"]
    assert events[-1]["status"] == "cancelled"


@pytest.mark.anyio
async def test_trace_cancelled_before_attaching_child_emits_no_events(
    tmp_path,
    monkeypatch,
) -> None:
    append_started = anyio.Event()

    async def block_before_append(_: RunContext, __, ___) -> None:
        append_started.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(RunContext, "_append_child", block_before_append)

    async def program(ctx: RunContext) -> None:
        await ctx.input("topic", "python")

    async def invoke() -> None:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="cancelled-before-trace-attachment",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await append_started.wait()
        task_group.cancel_scope.cancel()

    run_dir = anyio.Path(tmp_path, "cancelled-before-trace-attachment")
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())

    assert graph["root"]["children"] == []
    assert not await anyio.Path(run_dir, "progress.jsonl").exists()


@pytest.mark.anyio
async def test_input_and_binding_names_are_single_assignment(tmp_path) -> None:
    async def duplicate_input(ctx: RunContext) -> None:
        await ctx.input("topic", "first")
        await ctx.input("topic", "second")

    input_result = await run(
        duplicate_input,
        runs_dir=tmp_path,
        run_id="duplicate-input",
    )
    assert input_result.status == "error"

    async def duplicate_binding(ctx: RunContext) -> None:
        await ctx.save("answer", "first")
        await ctx.save("answer", "second")

    binding_result = await run(
        duplicate_binding,
        runs_dir=tmp_path,
        run_id="duplicate-binding",
    )
    assert binding_result.status == "error"
    assert (
        await anyio.Path(
            binding_result.run_dir,
            "bindings",
            "answer.md",
        ).read_text()
        == "first"
    )


@pytest.mark.anyio
async def test_failed_binding_serialization_does_not_commit_the_name(tmp_path) -> None:
    observed: list[str] = []

    async def program(ctx: RunContext) -> None:
        with pytest.raises(TypeError):
            await ctx.save("answer", cast("str", object()))
        await ctx.save("answer", "usable")
        observed.append("done")

    result = await run(program, runs_dir=tmp_path, run_id="retry-binding")

    assert result.status == "ok"
    assert observed == ["done"]
    assert await anyio.Path(result.run_dir, "bindings", "answer.md").read_text() == "usable"


@pytest.mark.anyio
async def test_binding_metadata_is_committed_after_content(
    tmp_path,
    monkeypatch,
) -> None:
    original_atomic_write_json = runtime_module._atomic_write_json
    metadata_committed = False

    async def assert_content_exists_first(
        path: anyio.Path,
        value: dict[str, object],
    ) -> None:
        nonlocal metadata_committed
        if path.name == "answer.meta.json":
            metadata_committed = True
            assert await anyio.Path(path.parent, "answer.md").read_text() == "usable"
        await original_atomic_write_json(path, value)

    monkeypatch.setattr(
        runtime_module,
        "_atomic_write_json",
        assert_content_exists_first,
    )

    async def program(ctx: RunContext) -> None:
        await ctx.save("answer", "usable")

    await run(
        program,
        runs_dir=tmp_path,
        run_id="binding-commit-marker",
        throw_on_error=True,
    )

    assert metadata_committed


@pytest.mark.anyio
async def test_binding_rollback_preserves_original_error_and_releases_name(
    tmp_path,
    monkeypatch,
) -> None:
    async def seed(ctx: RunContext) -> None:
        await ctx.save("answer", "old")

    await run(seed, runs_dir=tmp_path, run_id="binding-rollback-error")

    original_atomic_write_json = runtime_module._atomic_write_json
    original_atomic_write_text = runtime_module._atomic_write_text
    metadata_writes = 0
    restore_attempted = False

    async def fail_metadata_commit(
        path: anyio.Path,
        value: dict[str, object],
    ) -> None:
        nonlocal metadata_writes
        if path.name == "answer.meta.json":
            metadata_writes += 1
            if metadata_writes == 1:
                raise OSError("commit failed")
        await original_atomic_write_json(path, value)

    async def fail_binding_restore(path: anyio.Path, value: str) -> None:
        nonlocal restore_attempted
        if path.name == "answer.md" and value == "old" and not restore_attempted:
            restore_attempted = True
            raise OSError("rollback failed")
        await original_atomic_write_text(path, value)

    monkeypatch.setattr(runtime_module, "_atomic_write_json", fail_metadata_commit)
    monkeypatch.setattr(runtime_module, "_atomic_write_text", fail_binding_restore)

    async def resumed(ctx: RunContext) -> None:
        with pytest.raises(OSError, match="commit failed"):
            await ctx.save("answer", "new")
        assert not await anyio.Path(
            ctx._path,
            "bindings",
            "answer.meta.json",
        ).exists()
        await ctx.save("answer", "retry")

    result = await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id="binding-rollback-error",
        throw_on_error=True,
    )

    assert restore_attempted
    assert metadata_writes == 2
    assert await anyio.Path(result.run_dir, "bindings", "answer.md").read_text() == "retry"


@pytest.mark.anyio
async def test_binding_rollback_removes_commit_marker_when_metadata_restore_fails(
    tmp_path,
    monkeypatch,
) -> None:
    async def seed(ctx: RunContext) -> None:
        await ctx.save("answer", "old")

    result = await run(seed, runs_dir=tmp_path, run_id="metadata-rollback-error")
    original_atomic_write_json = runtime_module._atomic_write_json
    metadata_writes = 0

    async def fail_after_metadata_commit_then_fail_restore(
        path: anyio.Path,
        value: dict[str, object],
    ) -> None:
        nonlocal metadata_writes
        if path.name == "answer.meta.json":
            metadata_writes += 1
            if metadata_writes == 1:
                await original_atomic_write_json(path, value)
                raise OSError("commit failed")
            if metadata_writes == 2:
                raise OSError("restore failed")
        await original_atomic_write_json(path, value)

    monkeypatch.setattr(
        runtime_module,
        "_atomic_write_json",
        fail_after_metadata_commit_then_fail_restore,
    )

    async def resumed(ctx: RunContext) -> None:
        with pytest.raises(OSError, match="commit failed"):
            await ctx.save("answer", "new")

    await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id=result.run_id,
        throw_on_error=True,
    )

    bindings = anyio.Path(result.run_dir, "bindings")
    assert await anyio.Path(bindings, "answer.md").read_text() == "old"
    assert not await anyio.Path(bindings, "answer.meta.json").exists()


@pytest.mark.anyio
async def test_binding_rollback_preserves_cancellation_and_releases_name(
    tmp_path,
    monkeypatch,
) -> None:
    async def seed(ctx: RunContext) -> None:
        await ctx.save("answer", "old")

    await run(seed, runs_dir=tmp_path, run_id="binding-rollback-cancel")

    original_atomic_write_json = runtime_module._atomic_write_json
    original_atomic_write_text = runtime_module._atomic_write_text
    active_scope: anyio.CancelScope | None = None
    metadata_writes = 0
    restore_attempted = False

    async def cancel_metadata_commit(
        path: anyio.Path,
        value: dict[str, object],
    ) -> None:
        nonlocal metadata_writes
        if path.name == "answer.meta.json":
            metadata_writes += 1
            if metadata_writes == 1:
                assert active_scope is not None
                active_scope.cancel()
                await anyio.Event().wait()
        await original_atomic_write_json(path, value)

    async def fail_binding_restore(path: anyio.Path, value: str) -> None:
        nonlocal restore_attempted
        if path.name == "answer.md" and value == "old" and not restore_attempted:
            restore_attempted = True
            raise OSError("rollback failed")
        await original_atomic_write_text(path, value)

    monkeypatch.setattr(runtime_module, "_atomic_write_json", cancel_metadata_commit)
    monkeypatch.setattr(runtime_module, "_atomic_write_text", fail_binding_restore)

    async def resumed(ctx: RunContext) -> None:
        nonlocal active_scope
        with anyio.CancelScope() as scope:
            active_scope = scope
            await ctx.save("answer", "new")
        active_scope = None
        assert not await anyio.Path(
            ctx._path,
            "bindings",
            "answer.meta.json",
        ).exists()
        await ctx.save("answer", "retry")

    result = await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id="binding-rollback-cancel",
        throw_on_error=True,
    )

    assert restore_attempted
    assert metadata_writes == 2
    assert await anyio.Path(result.run_dir, "bindings", "answer.md").read_text() == "retry"


@pytest.mark.anyio
async def test_context_is_sealed_after_program_finishes(tmp_path) -> None:
    captured: list[RunContext] = []

    async def program(ctx: RunContext) -> None:
        captured.append(ctx)

    result = await run(program, runs_dir=tmp_path, run_id="sealed")
    assert result.status == "ok"

    with pytest.raises(RuntimeError, match="sealed"):
        await captured[0].save("late", "write")
    assert not await anyio.Path(result.run_dir, "bindings", "late.md").exists()


@pytest.mark.anyio
async def test_run_rejects_unsafe_or_conflicting_identifiers(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        return

    with pytest.raises(ValueError):
        await run(program, runs_dir=tmp_path, run_id="../escape")
    with pytest.raises(ValueError):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="new-run",
            resume_from_run_id="old-run",
        )
    with pytest.raises(ValueError, match='run_id "last" is reserved'):
        await run(program, runs_dir=tmp_path, run_id="last")
    with pytest.raises(ValueError):
        await run(program, runs_dir=tmp_path, run_id="")
    with pytest.raises(ValueError):
        await run(program, runs_dir=tmp_path, resume_from_run_id="")
    assert not await anyio.Path(tmp_path, "escape").exists()
    assert not await anyio.Path(tmp_path, "last").exists()
    assert not [path async for path in anyio.Path(tmp_path).iterdir()]


@pytest.mark.anyio
async def test_resume_reuses_the_existing_directory_without_erasing_bindings(
    tmp_path,
) -> None:
    async def first(ctx: RunContext) -> None:
        await ctx.save("answer", "cached")

    first_result = await run(first, runs_dir=tmp_path, run_id="resume-me")

    async def resumed(_: RunContext) -> None:
        return

    resumed_result = await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id="resume-me",
    )

    assert resumed_result.run_id == first_result.run_id
    assert resumed_result.run_dir == first_result.run_dir
    assert (
        await anyio.Path(
            resumed_result.run_dir,
            "bindings",
            "answer.md",
        ).read_text()
        == "cached"
    )
    meta = json.loads(await anyio.Path(resumed_result.run_dir, "meta.json").read_text())
    assert meta["resumed"] is True
    assert meta["resume_from_run_id"] == "resume-me"


@pytest.mark.anyio
async def test_resume_last_reuses_the_lexicographically_latest_run(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        return

    await run(program, runs_dir=tmp_path, run_id="20260101-old")
    latest = await run(program, runs_dir=tmp_path, run_id="20260102-new")

    resumed = await run(
        program,
        runs_dir=tmp_path,
        resume_from_run_id="last",
    )

    assert resumed.run_id == latest.run_id
    assert resumed.run_dir == latest.run_dir
    meta = json.loads(await anyio.Path(resumed.run_dir, "meta.json").read_text())
    assert meta["resume_from_run_id"] == latest.run_id


@pytest.mark.anyio
async def test_resume_last_skips_a_child_that_cannot_be_inspected(
    tmp_path,
    monkeypatch,
) -> None:
    async def program(_: RunContext) -> None:
        return

    valid = await run(program, runs_dir=tmp_path, run_id="valid-run")
    await anyio.Path(tmp_path, "unreadable-run").mkdir()
    original_is_dir = anyio.Path.is_dir

    async def fail_one_child(path: anyio.Path) -> bool:
        if path.name == "unreadable-run":
            raise OSError("unreadable")
        return await original_is_dir(path)

    monkeypatch.setattr(anyio.Path, "is_dir", fail_one_child)

    resumed = await run(
        program,
        runs_dir=tmp_path,
        resume_from_run_id="last",
    )

    assert resumed.run_id == valid.run_id
    assert resumed.run_dir == valid.run_dir


@pytest.mark.anyio
async def test_resume_requires_an_existing_run_directory(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        return

    with pytest.raises(FileNotFoundError, match="missing-run"):
        await run(
            program,
            runs_dir=tmp_path,
            resume_from_run_id="missing-run",
        )


@pytest.mark.anyio
async def test_gc_runs_keeps_count_days_union_and_explicit_exclusion(
    tmp_path,
) -> None:
    root = anyio.Path(tmp_path)
    for run_id in ("20260101-a", "20260102-b", "20260103-c", "20260104-d"):
        await anyio.Path(root, run_id).mkdir()
        await anyio.Path(root, run_id, "artifact.txt").write_text(run_id)
    await anyio.Path(root, "not-a-run.txt").write_text("keep")

    deleted = await gc_runs(
        root,
        keep_count=2,
        keep_days=0,
        exclude_run_id="20260101-a",
    )

    assert deleted == ("20260102-b",)
    assert await anyio.Path(root, "20260101-a").exists()
    assert await anyio.Path(root, "20260103-c").exists()
    assert await anyio.Path(root, "20260104-d").exists()
    assert await anyio.Path(root, "not-a-run.txt").exists()


@pytest.mark.anyio
async def test_gc_runs_keep_count_uses_run_id_not_mtime(tmp_path) -> None:
    root = anyio.Path(tmp_path)
    old_run = anyio.Path(root, "20260101-old")
    new_run = anyio.Path(root, "20260102-new")
    await old_run.mkdir()
    await new_run.mkdir()
    await run_sync_in_worker_thread(os.utime, str(old_run), (2, 2))
    await run_sync_in_worker_thread(os.utime, str(new_run), (1, 1))

    deleted = await gc_runs(root, keep_count=1, keep_days=0)

    assert deleted == ("20260101-old",)
    assert not await old_run.exists()
    assert await new_run.exists()


@pytest.mark.anyio
async def test_gc_runs_is_disabled_when_both_limits_are_zero(tmp_path) -> None:
    root = anyio.Path(tmp_path)
    run_dir = anyio.Path(root, "keep-me")
    marker = anyio.Path(run_dir, "artifact.txt")
    await run_dir.mkdir()
    await marker.write_text("keep")

    deleted = await gc_runs(root, keep_count=0, keep_days=0)

    assert deleted == ()
    assert await marker.read_text() == "keep"


@pytest.mark.anyio
async def test_run_accepts_gc_retention_configuration(tmp_path) -> None:
    old_run = anyio.Path(tmp_path, "old-run")
    await old_run.mkdir()

    async def program(_: RunContext) -> None:
        return

    await run(
        program,
        runs_dir=tmp_path,
        run_id="current-run",
        keep_count=0,
        keep_days=0,
        throw_on_error=True,
    )

    assert await old_run.exists()


@pytest.mark.anyio
async def test_run_duration_excludes_fresh_gc_time(
    tmp_path,
    monkeypatch,
) -> None:
    clock = 0.0

    async def slow_gc(*_: object, **__: object) -> tuple[str, ...]:
        nonlocal clock
        clock += 10
        return ()

    monkeypatch.setattr(runtime_module, "gc_runs", slow_gc)
    monkeypatch.setattr(runtime_module.time, "perf_counter", lambda: clock)

    async def program(_: RunContext) -> None:
        return

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="gc-before-timer",
        throw_on_error=True,
    )
    meta = json.loads(await anyio.Path(result.run_dir, "meta.json").read_text())

    assert meta["duration_ms"] == 0


@pytest.mark.anyio
async def test_run_log_reuses_persisted_duration(tmp_path, monkeypatch) -> None:
    ticks = iter((1.0, 1.125, 99.0))
    info_messages: list[str] = []
    monkeypatch.setattr(runtime_module.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(runtime_module.logger, "info", info_messages.append)

    async def program(_: RunContext) -> None:
        return

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="persisted-log-duration",
        throw_on_error=True,
    )
    meta = json.loads(await anyio.Path(result.run_dir, "meta.json").read_text())

    assert meta["duration_ms"] == 125
    assert info_messages[-1].endswith("in 125.0ms")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("keep_count", "keep_days", "error"),
    [
        (1.5, 7, TypeError),
        (50, float("nan"), ValueError),
        (50, 10**1_000, ValueError),
    ],
)
async def test_run_rejects_invalid_gc_configuration_before_creating_run(
    tmp_path,
    keep_count: object,
    keep_days: object,
    error: type[Exception],
) -> None:
    async def program(_: RunContext) -> None:
        pytest.fail("invalid cleanup configuration must fail before execution")

    with pytest.raises(error):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="invalid-gc",
            keep_count=cast("int", keep_count),
            keep_days=cast("int", keep_days),
        )

    assert not await anyio.Path(tmp_path, "invalid-gc").exists()


@pytest.mark.anyio
async def test_gc_runs_accepts_fractional_days(tmp_path) -> None:
    old_run = anyio.Path(tmp_path, "old")
    recent_run = anyio.Path(tmp_path, "recent")
    await old_run.mkdir()
    await recent_run.mkdir()
    await run_sync_in_worker_thread(os.utime, str(old_run), (1, 1))

    deleted = await gc_runs(tmp_path, keep_count=0, keep_days=0.5)

    assert deleted == ("old",)
    assert await recent_run.exists()


@pytest.mark.anyio
async def test_gc_runs_matches_exclusion_after_nfc_normalization(tmp_path) -> None:
    excluded = anyio.Path(tmp_path, "cafe\u0301")
    kept_by_count = anyio.Path(tmp_path, "zz-kept")
    await excluded.mkdir()
    await kept_by_count.mkdir()

    deleted = await gc_runs(
        tmp_path,
        keep_count=1,
        keep_days=0,
        exclude_run_id="caf\u00e9",
    )

    assert deleted == ()
    assert await excluded.exists()
    assert await kept_by_count.exists()


@pytest.mark.anyio
async def test_gc_runs_returns_empty_when_root_cannot_be_read(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path)
    original_resolve = anyio.Path.resolve

    async def fail_root_resolve(
        path: anyio.Path,
        strict: bool = False,
    ) -> anyio.Path:
        if path == root:
            raise OSError("unreadable")
        return await original_resolve(path, strict)

    monkeypatch.setattr(anyio.Path, "resolve", fail_root_resolve)

    assert await gc_runs(root, keep_count=1, keep_days=0) == ()


@pytest.mark.anyio
async def test_gc_runs_skips_unreadable_candidate_and_continues(
    tmp_path,
    monkeypatch,
) -> None:
    for run_id in ("broken", "healthy", "zz-kept"):
        await anyio.Path(tmp_path, run_id).mkdir()

    original_resolve = anyio.Path.resolve

    async def fail_one_resolve(
        path: anyio.Path,
        strict: bool = False,
    ) -> anyio.Path:
        if path.name == "broken":
            raise OSError("unreadable")
        return await original_resolve(path, strict)

    monkeypatch.setattr(anyio.Path, "resolve", fail_one_resolve)

    deleted = await gc_runs(tmp_path, keep_count=1, keep_days=0)

    assert deleted == ("healthy",)
    assert await anyio.Path(tmp_path, "broken").exists()
    assert not await anyio.Path(tmp_path, "healthy").exists()


async def _symlink_or_skip(
    link: anyio.Path,
    target: anyio.Path,
    *,
    target_is_directory: bool,
) -> None:
    try:
        await link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")


async def _directory_link_or_skip(
    link: anyio.Path,
    target: anyio.Path,
) -> None:
    try:
        await link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if sys.platform != "win32":
            pytest.skip(f"symbolic links are unavailable: {symlink_error}")

    result = await anyio.run_process(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            f"directory links are unavailable: {result.stderr.decode(errors='replace')}",
        )


async def _junction_or_skip(
    link: anyio.Path,
    target: anyio.Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("directory junctions are Windows-only")
    result = await anyio.run_process(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            f"directory junctions are unavailable: {result.stderr.decode(errors='replace')}",
        )


@pytest.mark.anyio
async def test_gc_runs_removes_junction_without_following_target(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    old_run = anyio.Path(root, "old-run")
    kept_run = anyio.Path(root, "zz-kept")
    outside = anyio.Path(tmp_path, "outside")
    marker = anyio.Path(outside, "marker.txt")
    await old_run.mkdir(parents=True)
    await outside.mkdir()
    await marker.write_text("keep")
    await _junction_or_skip(anyio.Path(old_run, "linked"), outside)
    await kept_run.mkdir()

    deleted = await gc_runs(root, keep_count=1, keep_days=0)

    assert deleted == ("old-run",)
    assert not await old_run.exists()
    assert await marker.read_text() == "keep"


@pytest.mark.anyio
async def test_resume_rejects_run_directory_symlink_escape(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    outside = anyio.Path(tmp_path, "outside")
    await root.mkdir()
    await outside.mkdir()
    await _directory_link_or_skip(
        anyio.Path(root, "escaped"),
        outside,
    )

    async def program(_: RunContext) -> None:
        pytest.fail("unsafe resume target must be rejected before execution")

    with pytest.raises(ValueError):
        await run(
            program,
            runs_dir=root,
            resume_from_run_id="escaped",
        )

    assert not await anyio.Path(outside, "input").exists()
    assert not await anyio.Path(outside, "bindings").exists()
    assert not await anyio.Path(outside, "trace").exists()


@pytest.mark.anyio
async def test_resume_rejects_bindings_directory_link_escape(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    run_dir = anyio.Path(root, "resume-me")
    outside = anyio.Path(tmp_path, "outside-bindings")
    await run_dir.mkdir(parents=True)
    await outside.mkdir()
    await anyio.Path(outside, "answer.md").write_text("secret")
    await _directory_link_or_skip(anyio.Path(run_dir, "bindings"), outside)

    async def program(_: RunContext) -> None:
        pytest.fail("unsafe bindings directory must be rejected before execution")

    with pytest.raises(ValueError):
        await run(
            program,
            runs_dir=root,
            resume_from_run_id="resume-me",
        )

    assert await anyio.Path(outside, "answer.md").read_text() == "secret"
    assert not await anyio.Path(run_dir, "input").exists()
    assert not await anyio.Path(run_dir, "trace").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("link_name", "target_value"),
    [
        ("answer.md", "secret"),
        ("answer.meta.json", '{"operation": "session"}'),
    ],
)
async def test_resume_rejects_binding_file_symlink_escape(
    tmp_path,
    link_name: str,
    target_value: str,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    run_dir = anyio.Path(root, "resume-me")
    bindings_dir = anyio.Path(run_dir, "bindings")
    await bindings_dir.mkdir(parents=True)
    outside = anyio.Path(tmp_path, f"outside-{link_name.replace('.', '-')}")
    await outside.write_text(target_value)
    await _symlink_or_skip(
        anyio.Path(bindings_dir, link_name),
        outside,
        target_is_directory=False,
    )

    async def program(_: RunContext) -> None:
        pytest.fail("unsafe binding target must be rejected before execution")

    with pytest.raises(ValueError):
        await run(
            program,
            runs_dir=root,
            resume_from_run_id="resume-me",
        )

    assert await outside.read_text() == target_value


@pytest.mark.anyio
async def test_run_rejects_inputs_that_collide_after_nfc_normalization(
    tmp_path,
) -> None:
    async def program(_: RunContext) -> None:
        pytest.fail("colliding inputs must be rejected before execution")

    with pytest.raises(ValueError):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="nfc-inputs",
            inputs={
                "cafe\u0301": "decomposed",
                "caf\u00e9": "precomposed",
            },
        )

    assert not await anyio.Path(tmp_path, "nfc-inputs").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("suffix", "value"),
    [
        (".md", "cached"),
        (".meta.json", '{"operation": "session"}'),
    ],
)
async def test_resume_rejects_files_that_collide_after_nfc_normalization(
    tmp_path,
    suffix: str,
    value: str,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    bindings_dir = anyio.Path(root, "resume-me", "bindings")
    await bindings_dir.mkdir(parents=True)
    await anyio.Path(bindings_dir, f"cafe\u0301{suffix}").write_text(value)
    await anyio.Path(bindings_dir, f"caf\u00e9{suffix}").write_text(value)

    async def program(_: RunContext) -> None:
        pytest.fail("colliding resume files must be rejected before execution")

    with pytest.raises(ValueError):
        await run(
            program,
            runs_dir=root,
            resume_from_run_id="resume-me",
        )


@pytest.mark.anyio
async def test_resume_treats_non_object_binding_metadata_as_cache_miss(
    tmp_path,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    bindings_dir = anyio.Path(root, "resume-me", "bindings")
    await bindings_dir.mkdir(parents=True)
    await anyio.Path(bindings_dir, "answer.md").write_text("cached")
    await anyio.Path(bindings_dir, "answer.meta.json").write_text("[]")
    calls = 0
    agent = flow.agent(AgentConfig(name="answer", system="Write."))

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        return "fresh"

    async def program(_: RunContext) -> None:
        assert await flow.session(agent, "prompt") == "fresh"

    await run(
        program,
        runs_dir=root,
        resume_from_run_id="resume-me",
        runner=runner,
        throw_on_error=True,
    )

    assert calls == 1
    assert await anyio.Path(bindings_dir, "answer.md").read_text() == "fresh"


@pytest.mark.anyio
async def test_resume_ignores_unreadable_unused_input(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    run_dir = anyio.Path(root, "resume-me")
    input_dir = anyio.Path(run_dir, "input")
    await input_dir.mkdir(parents=True)
    await anyio.Path(input_dir, "topic.md").write_bytes(b"\xff")
    await anyio.Path(run_dir, "program.py").write_text("old program\n")
    new_program = anyio.Path(tmp_path, "new-program.py")
    await new_program.write_text("new program\n")

    async def program(ctx: RunContext) -> None:
        await ctx.save("answer", "fresh")

    await run(
        program,
        runs_dir=root,
        resume_from_run_id="resume-me",
        program_path=new_program,
        throw_on_error=True,
    )

    assert await anyio.Path(run_dir, "program.py").read_text() == "new program\n"
    assert await anyio.Path(run_dir, "bindings", "answer.md").read_text() == "fresh"


@pytest.mark.anyio
async def test_resume_ignores_orphan_corrupt_metadata(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    run_dir = anyio.Path(root, "resume-me")
    input_dir = anyio.Path(run_dir, "input")
    bindings_dir = anyio.Path(run_dir, "bindings")
    await input_dir.mkdir(parents=True)
    await anyio.Path(input_dir, "topic.md").write_text("old input")
    await bindings_dir.mkdir()
    await anyio.Path(bindings_dir, "orphan.meta.json").write_text("{")
    await anyio.Path(run_dir, "program.py").write_text("old program\n")
    new_program = anyio.Path(tmp_path, "new-program.py")
    await new_program.write_text("new program\n")

    async def program(ctx: RunContext) -> None:
        await ctx.save("answer", "fresh")

    await run(
        program,
        runs_dir=root,
        resume_from_run_id="resume-me",
        program_path=new_program,
        throw_on_error=True,
    )

    assert await anyio.Path(run_dir, "program.py").read_text() == "new program\n"
    assert await anyio.Path(bindings_dir, "answer.md").read_text() == "fresh"


@pytest.mark.anyio
async def test_resume_skips_unreadable_binding(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    bindings_dir = anyio.Path(root, "resume-me", "bindings")
    await bindings_dir.mkdir(parents=True)
    await anyio.Path(bindings_dir, "answer.md").write_bytes(b"\xff")
    await anyio.Path(bindings_dir, "answer.meta.json").write_text(
        '{"operation": "session"}',
    )
    agent = flow.agent(AgentConfig(name="answer", system="Write."))

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return "fresh"

    async def program(_: RunContext) -> None:
        assert await flow.session(agent, "prompt") == "fresh"

    await run(
        program,
        runs_dir=root,
        resume_from_run_id="resume-me",
        runner=runner,
        throw_on_error=True,
    )

    assert await anyio.Path(bindings_dir, "answer.md").read_text() == "fresh"


@pytest.mark.anyio
async def test_resume_treats_unreadable_bindings_directory_as_empty_cache(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    bindings_dir = anyio.Path(root, "resume-me", "bindings")
    await bindings_dir.mkdir(parents=True)
    await anyio.Path(bindings_dir, "answer.md").write_text("cached")
    original_iterdir = anyio.Path.iterdir

    async def fail_bindings(path: anyio.Path):
        if path.name == "bindings":
            raise PermissionError("unreadable bindings")
        async for child in original_iterdir(path):
            yield child

    monkeypatch.setattr(anyio.Path, "iterdir", fail_bindings)
    calls = 0
    agent = flow.agent(AgentConfig(name="answer", system="Write."))

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        return "fresh"

    async def program(_: RunContext) -> None:
        assert await flow.session(agent, "prompt") == "fresh"

    await run(
        program,
        runs_dir=root,
        resume_from_run_id="resume-me",
        runner=runner,
        throw_on_error=True,
    )

    assert calls == 1


@pytest.mark.anyio
async def test_resume_treats_metadata_recursion_error_as_cache_miss(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    bindings_dir = anyio.Path(root, "resume-me", "bindings")
    await bindings_dir.mkdir(parents=True)
    await anyio.Path(bindings_dir, "answer.md").write_text("cached")
    metadata = '{"operation":"session"}'
    await anyio.Path(bindings_dir, "answer.meta.json").write_text(metadata)
    original_loads = json.loads

    def fail_metadata(value: str):
        if value == metadata:
            raise RecursionError("metadata nesting is too deep")
        return original_loads(value)

    monkeypatch.setattr(runtime_module.json, "loads", fail_metadata)
    calls = 0
    agent = flow.agent(AgentConfig(name="answer", system="Write."))

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        return "fresh"

    async def program(_: RunContext) -> None:
        assert await flow.session(agent, "prompt") == "fresh"

    await run(
        program,
        runs_dir=root,
        resume_from_run_id="resume-me",
        runner=runner,
        throw_on_error=True,
    )

    assert calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("directory_name", "artifact_name", "artifact_value"),
    [
        ("input", "e\u0301.md", "input"),
        ("bindings", "e\u0301.md", "binding"),
        ("bindings", "e\u0301.meta.json", "{}"),
    ],
)
async def test_resume_rejects_non_nfc_artifact_name_before_mutating_the_run(
    tmp_path,
    directory_name: str,
    artifact_name: str,
    artifact_value: str,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    run_dir = anyio.Path(root, "resume-me")
    artifact_dir = anyio.Path(run_dir, directory_name)
    await artifact_dir.mkdir(parents=True)
    await anyio.Path(artifact_dir, artifact_name).write_text(artifact_value)
    await anyio.Path(run_dir, "program.py").write_text("old program\n")
    new_program = anyio.Path(tmp_path, "new-program.py")
    await new_program.write_text("new program\n")

    async def program(_: RunContext) -> None:
        pytest.fail("non-NFC artifact names must be rejected before execution")

    with pytest.raises(ValueError, match="NFC"):
        await run(
            program,
            runs_dir=root,
            resume_from_run_id="resume-me",
            program_path=new_program,
            throw_on_error=True,
        )

    assert await anyio.Path(run_dir, "program.py").read_text() == "old program\n"
    assert not await anyio.Path(run_dir, "trace").exists()


@pytest.mark.anyio
async def test_atomic_write_preserves_write_error_when_temp_cleanup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    async def fail_write(_: anyio.Path, __: bytes) -> None:
        raise OSError("write failed")

    async def temp_exists(_: anyio.Path) -> bool:
        return True

    async def fail_unlink(_: anyio.Path) -> None:
        raise PermissionError("cleanup failed")

    monkeypatch.setattr(anyio.Path, "write_bytes", fail_write)
    monkeypatch.setattr(anyio.Path, "exists", temp_exists)
    monkeypatch.setattr(anyio.Path, "unlink", fail_unlink)

    with pytest.raises(OSError, match="write failed"):
        await runtime_module._atomic_write_bytes(
            anyio.Path(tmp_path, "artifact.bin"),
            b"value",
        )


@pytest.mark.anyio
async def test_atomic_write_preserves_cancellation_when_temp_cleanup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    active_scope: anyio.CancelScope | None = None

    async def cancel_write(_: anyio.Path, __: bytes) -> None:
        assert active_scope is not None
        active_scope.cancel()
        await anyio.sleep_forever()

    async def temp_exists(_: anyio.Path) -> bool:
        return True

    async def fail_unlink(_: anyio.Path) -> None:
        raise PermissionError("cleanup failed")

    monkeypatch.setattr(anyio.Path, "write_bytes", cancel_write)
    monkeypatch.setattr(anyio.Path, "exists", temp_exists)
    monkeypatch.setattr(anyio.Path, "unlink", fail_unlink)

    observed: BaseException | None = None
    with anyio.CancelScope() as scope:
        active_scope = scope
        try:
            await runtime_module._atomic_write_bytes(
                anyio.Path(tmp_path, "artifact.bin"),
                b"value",
            )
        except BaseException as error:
            observed = error

    assert isinstance(observed, anyio.get_cancelled_exc_class())


@pytest.mark.anyio
async def test_new_run_mkdir_and_cleanup_ownership_are_cancellation_atomic(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    active_scope: anyio.CancelScope | None = None
    returned_normally = False
    original_mkdir = anyio.Path.mkdir

    async def cancel_after_create(
        path: anyio.Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        await original_mkdir(
            path,
            mode=mode,
            parents=parents,
            exist_ok=exist_ok,
        )
        if path.name == "cancelled-after-mkdir":
            assert active_scope is not None
            active_scope.cancel()
            await checkpoint()

    monkeypatch.setattr(anyio.Path, "mkdir", cancel_after_create)

    async def program(_: RunContext) -> None:
        pytest.fail("program must not execute after initialization cancellation")

    with anyio.CancelScope() as scope:
        active_scope = scope
        await run(
            program,
            runs_dir=root,
            run_id="cancelled-after-mkdir",
        )
        returned_normally = True

    assert not returned_normally
    assert not await anyio.Path(root, "cancelled-after-mkdir").exists()


@pytest.mark.anyio
async def test_explicit_program_snapshot_failure_does_not_abort_run(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    program_path = anyio.Path(tmp_path, "program.py")
    await program_path.write_text("async def main(ctx):\n    return None\n")
    original_atomic_write = runtime_module._atomic_write_bytes

    async def fail_program_write(path: anyio.Path, value: bytes) -> None:
        if path.name == "program.py":
            raise OSError("disk full")
        await original_atomic_write(path, value)

    monkeypatch.setattr(runtime_module, "_atomic_write_bytes", fail_program_write)

    executed = False

    async def program(_: RunContext) -> None:
        nonlocal executed
        executed = True

    result = await run(
        program,
        runs_dir=root,
        run_id="snapshot-failed",
        program_path=program_path,
        throw_on_error=True,
    )

    assert executed
    assert result.status == "ok"
    meta = json.loads(
        await anyio.Path(root, "snapshot-failed", "meta.json").read_text(),
    )
    assert meta["program_snapshot"] == f"unavailable: {program_path}"


@pytest.mark.anyio
async def test_gc_runs_continues_after_one_directory_cannot_be_deleted(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path)
    for run_id in ("broken", "healthy", "zz-kept"):
        await anyio.Path(root, run_id).mkdir()
        await anyio.Path(root, run_id, "artifact.txt").write_text(run_id)

    original_remove_tree = runtime_module._remove_tree
    attempted: set[str] = set()

    async def fail_one_directory(path: anyio.Path) -> None:
        attempted.add(path.name)
        if path.name == "broken":
            raise OSError("directory is busy")
        await original_remove_tree(path)

    monkeypatch.setattr(runtime_module, "_remove_tree", fail_one_directory)

    deleted = await gc_runs(root, keep_count=1, keep_days=0)

    assert attempted == {"broken", "healthy"}
    assert deleted == ("healthy",)
    assert await anyio.Path(root, "broken").exists()
    assert not await anyio.Path(root, "healthy").exists()
    assert await anyio.Path(root, "zz-kept").exists()


@pytest.mark.anyio
async def test_gc_runs_retries_read_only_file_deletion(
    tmp_path,
    monkeypatch,
) -> None:
    old_run = anyio.Path(tmp_path, "old")
    kept_run = anyio.Path(tmp_path, "zz-kept")
    read_only = anyio.Path(old_run, "readonly.txt")
    await old_run.mkdir()
    await kept_run.mkdir()
    await read_only.write_text("data")
    original_unlink = anyio.Path.unlink
    failed_once = False

    async def fail_once(
        path: anyio.Path,
        missing_ok: bool = False,
    ) -> None:
        nonlocal failed_once
        if path == read_only and not failed_once:
            failed_once = True
            raise PermissionError("read-only")
        await original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(anyio.Path, "unlink", fail_once)

    assert await gc_runs(tmp_path, keep_count=1, keep_days=0) == ("old",)
    assert failed_once
    assert not await old_run.exists()


@pytest.mark.anyio
async def test_gc_runs_shields_each_deletion_but_cancels_between_candidates(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path)
    for run_id in ("a-later", "b-removing", "zz-kept"):
        await anyio.Path(root, run_id).mkdir()
        await anyio.Path(root, run_id, "artifact.txt").write_text(run_id)

    removal_started = anyio.Event()
    release_removal = anyio.Event()
    attempted: list[str] = []
    returned_normally = False
    original_remove_tree = runtime_module._remove_tree

    async def block_first_removal(path: anyio.Path) -> None:
        attempted.append(path.name)
        if path.name == "b-removing":
            await anyio.Path(path, "artifact.txt").unlink()
            removal_started.set()
            await release_removal.wait()
        await original_remove_tree(path)

    monkeypatch.setattr(runtime_module, "_remove_tree", block_first_removal)

    async def invoke() -> None:
        nonlocal returned_normally
        await gc_runs(root, keep_count=1, keep_days=0)
        returned_normally = True

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await removal_started.wait()
        task_group.cancel_scope.cancel()
        release_removal.set()

    assert not returned_normally
    assert attempted == ["b-removing"]
    assert not await anyio.Path(root, "b-removing").exists()
    assert await anyio.Path(root, "a-later", "artifact.txt").read_text() == "a-later"
    assert await anyio.Path(root, "zz-kept").exists()


@pytest.mark.anyio
async def test_cancelled_input_read_releases_name_for_retry(
    tmp_path,
    monkeypatch,
) -> None:
    original_atomic_write = runtime_module._atomic_write_text
    first_write_started = anyio.Event()
    first_write = True

    async def block_first_input_write(path: anyio.Path, value: str) -> None:
        nonlocal first_write
        if path.name == "topic.md" and first_write:
            first_write = False
            first_write_started.set()
            await anyio.sleep_forever()
        await original_atomic_write(path, value)

    monkeypatch.setattr(
        runtime_module,
        "_atomic_write_text",
        block_first_input_write,
    )

    async def program(ctx: RunContext) -> None:
        scopes: list[anyio.CancelScope] = []

        async def cancelled_read() -> None:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await ctx.input("topic", "first")

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancelled_read)
            await first_write_started.wait()
            scopes[0].cancel()

        assert await ctx.input("topic", "second") == "second"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="cancelled-input",
    )

    assert result.status == "ok"
    assert (
        await anyio.Path(
            result.run_dir,
            "input",
            "topic.md",
        ).read_text()
        == "second"
    )
