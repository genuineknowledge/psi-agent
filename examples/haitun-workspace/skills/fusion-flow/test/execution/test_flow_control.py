from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import cast

import anyio
import pytest
from fusion_flow.execution import PipelineStep, RunContext, flow, run
from fusion_flow.execution.flow import logger as flow_logger


async def _execute(
    tmp_path,
    run_id: str,
    body: Callable[[RunContext], Awaitable[None]],
) -> RunContext:
    contexts: list[RunContext] = []

    async def program(context: RunContext) -> None:
        contexts.append(context)
        await body(context)

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id=run_id,
        throw_on_error=True,
    )
    assert result.status == "ok"
    return contexts[0]


@pytest.mark.anyio
async def test_parallel_all_preserves_input_order_and_none_values(tmp_path) -> None:
    async def body(_: RunContext) -> None:
        async def delayed(value: object, delay: float) -> object:
            await anyio.sleep(delay)
            return value

        tasks = [
            partial(delayed, None, 0.01),
            partial(delayed, "second", 0),
            partial(delayed, "third", 0.005),
        ]
        assert await flow.parallel(tasks, join="all") == [None, "second", "third"]

    context = await _execute(tmp_path, "parallel-all", body)
    trace = context.root_trace.children[0]
    assert (trace.kind, trace.label, trace.status) == ("parallel", "all", "ok")
    assert trace.metadata == {
        "task_count": 3,
        "join": "all",
        "any_count": None,
    }


@pytest.mark.anyio
async def test_parallel_first_uses_first_settlement_and_records_real_index(
    tmp_path,
) -> None:
    settled: list[str] = []

    async def body(_: RunContext) -> None:
        slow_started = anyio.Event()

        async def slow() -> None:
            slow_started.set()
            try:
                await anyio.sleep_forever()
            finally:
                settled.append("slow")

        async def fast() -> str:
            await slow_started.wait()
            return "fast"

        assert await flow.parallel([slow, fast], join="first") == ["fast"]
        assert settled == ["slow"]

    context = await _execute(tmp_path, "parallel-first", body)
    trace = context.root_trace.children[0]
    assert trace.metadata["selected_index"] == 1


@pytest.mark.anyio
async def test_parallel_any_returns_completion_order_and_settles_laggards(
    tmp_path,
) -> None:
    settled: list[str] = []

    async def body(_: RunContext) -> None:
        slow_started = anyio.Event()

        async def slow() -> None:
            slow_started.set()
            try:
                await anyio.sleep_forever()
            finally:
                settled.append("slow")

        async def first() -> str:
            await slow_started.wait()
            return "first"

        async def second() -> str:
            await slow_started.wait()
            await anyio.sleep(0.001)
            return "second"

        assert await flow.parallel(
            [slow, first, second],
            join="any",
            any_count=2,
        ) == ["first", "second"]
        assert settled == ["slow"]

    await _execute(tmp_path, "parallel-any", body)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("join", "any_count"),
    [("all", None), ("first", None), ("any", 1)],
)
async def test_parallel_failure_cancels_and_waits_for_siblings(
    tmp_path,
    join: str,
    any_count: int | None,
) -> None:
    settled = anyio.Event()

    async def body(_: RunContext) -> None:
        slow_started = anyio.Event()

        async def slow() -> None:
            slow_started.set()
            try:
                await anyio.sleep_forever()
            finally:
                settled.set()

        async def fail() -> str:
            await slow_started.wait()
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await flow.parallel(
                [slow, fail],
                join=join,
                any_count=any_count,
            )
        assert settled.is_set()

    context = await _execute(tmp_path, f"parallel-failure-{join}", body)
    trace = context.root_trace.children[0]
    assert trace.status == "error"
    assert trace.error == "boom"


@pytest.mark.anyio
async def test_parallel_validates_join_and_any_count_before_starting_tasks(
    tmp_path,
) -> None:
    called = False

    async def body(_: RunContext) -> None:
        nonlocal called

        async def task() -> int:
            nonlocal called
            called = True
            return 1

        assert await flow.parallel([], join="all") == []
        with pytest.raises(ValueError, match="at least one"):
            await flow.parallel([], join="first")
        with pytest.raises(ValueError, match="at least one"):
            await flow.parallel([], join="any", any_count=1)
        with pytest.raises(ValueError, match="unsupported"):
            await flow.parallel([task], join="last")
        for invalid in (None, True, 0, 2):
            with pytest.raises((TypeError, ValueError)):
                await flow.parallel([task], join="any", any_count=invalid)
        assert called is False

    await _execute(tmp_path, "parallel-validation", body)


@pytest.mark.anyio
async def test_if_variants_require_bool_and_trace_selected_index(tmp_path) -> None:
    called: list[str] = []

    async def body(_: RunContext) -> None:
        async def branch(label: str) -> str:
            called.append(label)
            return label

        with pytest.raises(TypeError, match="bool"):
            await flow.if_(cast("bool", 1), partial(branch, "invalid"))
        with pytest.raises(TypeError, match="branch 1"):
            await flow.if_else(
                [
                    (True, partial(branch, "must-not-run")),
                    (cast("bool", 1), partial(branch, "invalid")),
                ],
            )

        assert (
            await flow.if_(
                False,
                partial(branch, "then"),
                partial(branch, "else"),
            )
            == "else"
        )
        assert (
            await flow.if_else(
                [
                    (False, partial(branch, "zero")),
                    (True, partial(branch, "one")),
                    (True, partial(branch, "two")),
                ],
            )
            == "one"
        )

    context = await _execute(tmp_path, "if-selection", body)
    assert called == ["else", "one"]
    traces = [trace for trace in context.root_trace.children if trace.kind == "if"]
    assert [trace.metadata["selected_index"] for trace in traces] == [1, 1]
    assert [trace.children[0].label for trace in traces] == ["else", "branch-1"]


@pytest.mark.anyio
async def test_for_each_is_serial_and_parallel_for_each_is_concurrent(
    tmp_path,
) -> None:
    serial: list[tuple[str, int]] = []
    parallel: list[int] = []

    async def body(_: RunContext) -> None:
        async def visit_serial(item: str, index: int) -> None:
            serial.append((item, index))
            await anyio.sleep(0.001)

        assert await flow.for_each(["a", "b"], visit_serial) is None

        release = anyio.Event()

        async def visit_parallel(_: str, index: int) -> None:
            parallel.append(index)
            if len(parallel) == 3:
                release.set()
            await release.wait()

        with anyio.fail_after(1):
            assert (
                await flow.parallel_for_each(
                    ["a", "b", "c"],
                    visit_parallel,
                )
                is None
            )

    context = await _execute(tmp_path, "for-each", body)
    assert serial == [("a", 0), ("b", 1)]
    assert sorted(parallel) == [0, 1, 2]
    traces = context.root_trace.children
    assert [(trace.kind, trace.metadata["parallel"]) for trace in traces] == [
        ("forEach", False),
        ("forEach", True),
    ]
    assert [[child.metadata["index"] for child in trace.children] for trace in traces] == [
        [0, 1],
        [0, 1, 2],
    ]


@pytest.mark.anyio
async def test_loops_have_do_until_and_while_semantics_and_record_caps(
    tmp_path,
) -> None:
    until_count = 0
    while_count = 0
    initial_metadata: list[dict[str, object]] = []

    async def body(context: RunContext) -> None:
        nonlocal until_count, while_count

        async def until_step(_: int) -> None:
            nonlocal until_count
            initial_metadata.append(
                dict(context.root_trace.children[-1].metadata),
            )
            until_count += 1

        async def while_step(_: int) -> None:
            nonlocal while_count
            while_count += 1

        await flow.loop_until(
            lambda: until_count == 2,
            until_step,
            max_iterations=4,
        )
        await flow.loop_while(
            lambda: while_count < 2,
            while_step,
            max_iterations=4,
        )
        await flow.loop_until(lambda: False, until_step, max_iterations=1)

        with pytest.raises(TypeError, match="bool"):
            await flow.loop_while(
                lambda: cast("bool", 1),
                while_step,
            )
        for invalid in (True, 0, -1, 1.5):
            with pytest.raises(ValueError, match="positive integer"):
                await flow.loop_until(
                    lambda: True,
                    until_step,
                    max_iterations=cast("int", invalid),
                )

    context = await _execute(tmp_path, "loops", body)
    assert (until_count, while_count) == (3, 2)
    traces = [trace for trace in context.root_trace.children if trace.kind == "loop"]
    assert [trace.metadata["iterations"] for trace in traces[:3]] == [2, 2, 1]
    assert [trace.metadata["hit_max_iterations"] for trace in traces[:3]] == [
        False,
        False,
        True,
    ]
    assert [trace.metadata["max_iterations"] for trace in traces[:3]] == [4, 4, 1]
    assert initial_metadata[:2] == [
        {
            "loop_kind": "until",
            "iterations": 0,
            "max_iterations": 4,
            "hit_max_iterations": False,
        },
        {
            "loop_kind": "until",
            "iterations": 1,
            "max_iterations": 4,
            "hit_max_iterations": False,
        },
    ]


@pytest.mark.anyio
async def test_collection_operations_preserve_contracts_and_order(tmp_path) -> None:
    async def body(_: RunContext) -> None:
        async def double(item: int, _: int) -> int:
            return item * 2

        async def delayed_double(item: int, _: int) -> int:
            await anyio.sleep((4 - item) * 0.002)
            return item * 2

        async def is_odd(item: int, _: int) -> bool:
            return item % 2 == 1

        async def delayed_is_odd(item: int, _: int) -> bool:
            await anyio.sleep((4 - item) * 0.002)
            return item % 2 == 1

        async def not_a_bool(_: int, __: int) -> bool:
            return cast("bool", 1)

        async def add(total: int, item: int, index: int) -> int:
            return total + item + index

        items = [1, 2, 3]
        assert await flow.map(items, double) == [2, 4, 6]
        assert await flow.pmap(items, delayed_double) == [2, 4, 6]
        assert await flow.filter(items, is_odd) == [1, 3]
        assert await flow.pfilter(items, delayed_is_odd) == [1, 3]
        with pytest.raises(TypeError, match="predicate must return bool"):
            await flow.filter(items, not_a_bool)
        with pytest.raises(TypeError, match="predicate must return bool"):
            await flow.pfilter(items, not_a_bool)
        assert await flow.reduce(items, add, 0) == 9

    await _execute(tmp_path, "collections", body)


@pytest.mark.anyio
async def test_pmap_uses_the_initial_item_count(tmp_path) -> None:
    items = [1, 2]

    async def body(_: RunContext) -> None:
        async def mutate(item: int, index: int) -> int:
            if index == 0:
                items.append(3)
            return item * 2

        assert await flow.pmap(items, mutate) == [2, 4]

    await _execute(tmp_path, "pmap-initial-count", body)


@pytest.mark.anyio
async def test_pipeline_threads_values_and_records_step_children(tmp_path) -> None:
    async def body(_: RunContext) -> None:
        async def increment(value: object) -> object:
            assert isinstance(value, int)
            return value + 1

        async def render(value: object) -> object:
            return f"value={value}"

        assert (
            await flow.pipeline(
                1,
                [
                    PipelineStep(label="", fn=increment),
                    PipelineStep(fn=render),
                ],
            )
            == "value=2"
        )

    context = await _execute(tmp_path, "pipeline", body)
    trace = context.root_trace.children[0]
    assert (trace.kind, trace.status, trace.output_summary) == (
        "pipeline",
        "ok",
        "'value=2'",
    )
    assert [child.kind for child in trace.children] == [
        "pipelineStep",
        "pipelineStep",
    ]
    assert trace.metadata == {"step_count": 2}
    assert [child.label for child in trace.children] == ["", "1"]
    assert [child.metadata["index"] for child in trace.children] == [0, 1]


@pytest.mark.anyio
async def test_retry_counts_total_attempts_and_skips_permanent_errors(
    tmp_path,
    monkeypatch,
) -> None:
    attempts = 0
    permanent_attempts = 0
    warnings: list[str] = []
    monkeypatch.setattr(flow_logger, "warning", warnings.append)

    async def body(_: RunContext) -> None:
        nonlocal attempts, permanent_attempts

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("temporary")
            return "ok"

        async def permanent() -> str:
            nonlocal permanent_attempts
            permanent_attempts += 1
            raise ValueError("invalid")

        assert (
            await flow.retry(
                flaky,
                max_attempts=3,
                initial_delay=0,
            )
            == "ok"
        )
        with pytest.raises(ValueError, match="invalid"):
            await flow.retry(
                permanent,
                max_attempts=3,
                initial_delay=0,
                should_retry=lambda _error, _attempt: False,
            )

    context = await _execute(tmp_path, "retry", body)
    assert (attempts, permanent_attempts) == (3, 1)
    traces = [trace for trace in context.root_trace.children if trace.kind == "retry"]
    assert [trace.metadata["attempts"] for trace in traces] == [3, 1]
    assert [trace.status for trace in traces] == ["ok", "error"]
    retry_warnings = [warning for warning in warnings if "retry attempt" in warning]
    assert len(retry_warnings) == 2
    assert all("temporary" in warning for warning in retry_warnings)


@pytest.mark.anyio
async def test_retry_recomputes_backoff_before_clamping(
    tmp_path,
    monkeypatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(anyio, "sleep", sleep)

    async def body(_: RunContext) -> None:
        nonlocal attempts

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise RuntimeError("temporary")
            return "ok"

        assert (
            await flow.retry(
                flaky,
                max_attempts=4,
                initial_delay=0.01,
                backoff_factor=0.8,
                max_delay=0.005,
            )
            == "ok"
        )

    await _execute(tmp_path, "retry-decreasing-backoff", body)
    assert delays == [0.005, 0.005, 0.005]


@pytest.mark.anyio
async def test_retry_validates_backoff_and_never_retries_cancellation(
    tmp_path,
) -> None:
    attempts = 0

    async def body(_: RunContext) -> None:
        nonlocal attempts

        async def succeeds() -> str:
            return "ok"

        with pytest.raises(ValueError):
            await flow.retry(succeeds, initial_delay=-1)
        with pytest.raises(ValueError):
            await flow.retry(succeeds, backoff_factor=0)
        with pytest.raises(ValueError):
            await flow.retry(succeeds, max_delay=-1)

        async def cancelled() -> None:
            nonlocal attempts
            attempts += 1
            await anyio.sleep_forever()

        with anyio.move_on_after(0.05) as scope:
            await flow.retry(cancelled, initial_delay=0)
        assert scope.cancel_called

    context = await _execute(tmp_path, "retry-validation", body)
    assert attempts == 1
    trace = [trace for trace in context.root_trace.children if trace.kind == "retry"][-1]
    assert trace.status == "cancelled"


@pytest.mark.anyio
async def test_repeat_accepts_zero_and_passes_each_index(tmp_path) -> None:
    visited: list[int] = []

    async def body(_: RunContext) -> None:
        async def visit(index: int) -> None:
            visited.append(index)

        assert await flow.repeat(0, visit) is None
        assert await flow.repeat(3, visit) is None
        for invalid in (True, -1, 1.5):
            with pytest.raises(ValueError, match="non-negative integer"):
                await flow.repeat(cast("int", invalid), visit)

    await _execute(tmp_path, "repeat", body)
    assert visited == [0, 1, 2]


@pytest.mark.anyio
async def test_inline_and_named_blocks_execute_and_trace_without_overwriting(
    tmp_path,
) -> None:
    async def body(_: RunContext) -> None:
        async def add(args: dict[str, str]) -> int:
            return int(args["left"]) + int(args["right"])

        async def inline() -> str:
            return "inline"

        handle = flow.define_block("add", add, description="sum")
        with pytest.raises(ValueError, match="unsafe character"):
            await flow.run_block("missing/block")
        with pytest.raises(ValueError, match="unsafe character"):
            await flow.use("missing/service")
        assert (handle.name, handle.description, handle.kind) == ("add", "sum", "block")
        with pytest.raises(ValueError, match="already defined"):
            flow.define_block("add", add)
        assert await flow.run_block(handle, {"left": "2", "right": "3"}) == 5
        with pytest.raises(ValueError, match="not defined"):
            await flow.run_block("missing")
        assert await flow.block("inline", inline) == "inline"
        assert await flow.block("inline", inline) == "inline"

    context = await _execute(tmp_path, "blocks", body)
    traces = [trace for trace in context.root_trace.children if trace.kind == "block"]
    assert [(trace.label, trace.output_summary) for trace in traces] == [
        ("add", "5"),
        ("inline", "'inline'"),
        ("inline", "'inline'"),
    ]
