from __future__ import annotations

import json
import os
import sys
from typing import cast

import anyio
import pytest
from anyio.to_thread import run_sync as run_sync_in_worker_thread

from psi_agent.fusion_flow import runtime as runtime_module
from psi_agent.fusion_flow.flow import flow
from psi_agent.fusion_flow.runtime import RunContext, gc_runs, run


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
    assert [event["event"] for event in events] == ["node_end"]
    assert events[0]["status"] == "cancelled"


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
    assert not await anyio.Path(tmp_path, "escape").exists()


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
async def test_resume_rejects_non_object_binding_metadata(tmp_path) -> None:
    root = anyio.Path(tmp_path, "runs")
    bindings_dir = anyio.Path(root, "resume-me", "bindings")
    await bindings_dir.mkdir(parents=True)
    await anyio.Path(bindings_dir, "answer.md").write_text("cached")
    await anyio.Path(bindings_dir, "answer.meta.json").write_text("[]")

    async def program(_: RunContext) -> None:
        pytest.fail("corrupt metadata must be rejected before execution")

    with pytest.raises(ValueError, match="object"):
        await run(
            program,
            runs_dir=root,
            resume_from_run_id="resume-me",
        )


@pytest.mark.anyio
async def test_fresh_run_initialization_failure_removes_new_directory(
    tmp_path,
    monkeypatch,
) -> None:
    root = anyio.Path(tmp_path, "runs")
    program_path = anyio.Path(tmp_path, "program.py")
    await program_path.write_text("async def main(ctx):\n    return None\n")

    async def fail_atomic_write(_: anyio.Path, __: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(runtime_module, "_atomic_write_text", fail_atomic_write)

    async def program(_: RunContext) -> None:
        pytest.fail("program must not execute after initialization failure")

    with pytest.raises(OSError, match="disk full"):
        await run(
            program,
            runs_dir=root,
            run_id="init-failed",
            program_path=program_path,
        )

    assert not await anyio.Path(root, "init-failed").exists()


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
