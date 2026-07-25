from __future__ import annotations

import getpass
import json
import math
import sys
from collections.abc import Callable
from typing import cast

import anyio
import pytest

from psi_agent.fusion_flow.flow import flow
from psi_agent.fusion_flow.model import (
    AgentConfig,
    AgentInvocation,
    ContainsRule,
    EqualsRule,
    ExecResult,
    PredicateRule,
    RangeRule,
    RegexRule,
    SessionResult,
    StaticRule,
)
from psi_agent.fusion_flow.runtime import RunContext, run


@pytest.mark.anyio
async def test_evaluate_parses_all_kinds_and_persists_parsed_bindings_and_traces(
    tmp_path,
) -> None:
    responses = iter(
        (
            SessionResult('```json\n{"value": true}\n```', input_tokens=3, output_tokens=1),
            SessionResult('{"value": "9.6"}'),
            SessionResult('{"value": " BLUE "}'),
        )
    )

    async def runner(_: AgentConfig, __: AgentInvocation) -> SessionResult:
        return next(responses)

    observed: list[object] = []

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.evaluate(
                question="boolean?",
                kind="boolean",
                binding_name="boolean-result",
            )
        )
        observed.append(
            await flow.evaluate(
                question="number?",
                kind="number",
                minimum=0,
                maximum=5,
                integer=True,
                binding_name="number-result",
            )
        )
        observed.append(
            await flow.evaluate(
                question="choice?",
                kind="choice",
                choices=("red", "blue"),
                binding_name="choice-result",
            )
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        runner=runner,
        run_id="evaluate-kinds",
        throw_on_error=True,
    )

    assert observed == [True, 5, "blue"]
    run_dir = anyio.Path(result.run_dir)
    for name, expected in (
        ("boolean-result", True),
        ("number-result", 5),
        ("choice-result", "blue"),
    ):
        binding = json.loads(await anyio.Path(run_dir, "bindings", f"{name}.md").read_text())
        trace = json.loads(await anyio.Path(run_dir, "trace", f"{name}.json").read_text())
        metadata = json.loads(await anyio.Path(run_dir, "bindings", f"{name}.meta.json").read_text())
        assert binding == {"value": expected}
        assert trace["kind"] == "evaluate"
        assert trace["status"] == "ok"
        assert metadata["operation"] == "evaluate"

    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    assert [child["kind"] for child in graph["root"]["children"]] == [
        "evaluate",
        "evaluate",
        "evaluate",
    ]
    assert graph["root"]["children"][0]["tokens"] == {
        "calls": 1,
        "input": 3,
        "output": 1,
    }
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["tokens"]["user"] == {"calls": 0, "input": 0, "output": 0}
    assert meta["tokens"]["internal"] == {
        "calls": 3,
        "input": None,
        "output": None,
    }


@pytest.mark.anyio
async def test_evaluate_rejects_invalid_configurations_before_invocation() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        await flow.evaluate(question="q", kind="text")
    with pytest.raises(ValueError, match="non-empty"):
        await flow.evaluate(question="q", kind="choice")
    with pytest.raises(ValueError, match="unique"):
        await flow.evaluate(
            question="q",
            kind="choice",
            choices=("same", "same"),
        )
    with pytest.raises(ValueError, match="minimum"):
        await flow.evaluate(
            question="q",
            kind="number",
            minimum=2,
            maximum=1,
        )


@pytest.mark.anyio
async def test_failed_evaluate_does_not_commit_a_binding(tmp_path) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return '{"value": "not-a-bool"}'

    async def program(_: RunContext) -> None:
        await flow.evaluate(
            question="boolean?",
            kind="boolean",
            binding_name="invalid-evaluation",
        )

    with pytest.raises(TypeError, match="bool"):
        await run(
            program,
            runs_dir=tmp_path,
            runner=runner,
            run_id="invalid-evaluate",
            throw_on_error=True,
        )

    assert not await anyio.Path(
        tmp_path,
        "invalid-evaluate",
        "bindings",
        "invalid-evaluation.md",
    ).exists()


@pytest.mark.anyio
async def test_number_evaluate_rejects_boolean_result(tmp_path) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return '{"value": true}'

    async def program(_: RunContext) -> None:
        await flow.evaluate(question="number?", kind="number")

    with pytest.raises(TypeError, match="number"):
        await run(
            program,
            runs_dir=tmp_path,
            runner=runner,
            run_id="boolean-number",
            throw_on_error=True,
        )


@pytest.mark.anyio
async def test_evaluate_static_supports_each_rule_and_persists_trace(tmp_path) -> None:
    observed: list[bool] = []

    async def is_expected() -> bool:
        await anyio.sleep(0.001)
        return True

    async def program(_: RunContext) -> None:
        observed.extend(
            (
                await flow.evaluate_static(
                    question="contains digits",
                    rule=RegexRule(pattern=r"\d+", on="abc123"),
                    binding_name="regex-result",
                ),
                await flow.evaluate_static(
                    question="contains text",
                    rule=ContainsRule(needle="ell", on="hello"),
                    binding_name="contains-result",
                ),
                await flow.evaluate_static(
                    question="same text",
                    rule=EqualsRule(expected="same", on="same"),
                    binding_name="equals-result",
                ),
                await flow.evaluate_static(
                    question="inside range",
                    rule=RangeRule(value=5, minimum=1, maximum=5),
                    binding_name="range-result",
                ),
                await flow.evaluate_static(
                    question="custom predicate",
                    rule=PredicateRule(fn=is_expected),
                    binding_name="predicate-result",
                ),
            )
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="static-rules",
        throw_on_error=True,
    )

    assert observed == [True] * 5
    run_dir = anyio.Path(result.run_dir)
    for name in (
        "regex-result",
        "contains-result",
        "equals-result",
        "range-result",
        "predicate-result",
    ):
        payload = json.loads(await anyio.Path(run_dir, "bindings", f"{name}.md").read_text())
        metadata = json.loads(await anyio.Path(run_dir, "bindings", f"{name}.meta.json").read_text())
        assert payload["value"] is True
        assert payload["rule"] == name.removesuffix("-result")
        assert metadata["operation"] == "evaluate_static"

    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    assert [(child["kind"], child["label"]) for child in graph["root"]["children"]] == [
        ("evaluate", "static"),
    ] * 5


@pytest.mark.anyio
async def test_evaluate_static_reserves_binding_before_predicate(tmp_path) -> None:
    calls = 0

    def predicate() -> bool:
        nonlocal calls
        calls += 1
        return True

    async def program(_: RunContext) -> None:
        await flow.evaluate_static(
            question="first",
            rule=PredicateRule(fn=predicate),
            binding_name="static-result",
        )
        with pytest.raises(ValueError, match="already exists"):
            await flow.evaluate_static(
                question="second",
                rule=PredicateRule(fn=predicate),
                binding_name="static-result",
            )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="static-binding-reservation",
        throw_on_error=True,
    )

    assert calls == 1
    assert result.status == "ok"


@pytest.mark.anyio
async def test_evaluate_static_validates_rule_shape_and_results(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        with pytest.raises(TypeError, match="StaticRule"):
            await flow.evaluate_static(
                question="invalid",
                rule=cast("StaticRule", object()),
            )
        with pytest.raises(TypeError, match="bool"):
            await flow.evaluate_static(
                question="invalid predicate",
                rule=PredicateRule(
                    fn=cast("Callable[[], bool]", lambda: "yes"),
                ),
            )

    with pytest.raises(ValueError, match="minimum"):
        RangeRule(value=1, minimum=2, maximum=1)
    with pytest.raises(TypeError, match="numeric"):
        RangeRule(value=cast("float", "1"))

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="static-validation",
        throw_on_error=True,
    )
    assert result.status == "ok"


@pytest.mark.anyio
async def test_choice_uses_default_only_when_evaluation_fails(tmp_path) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return "not json"

    selected: list[str] = []

    async def primary() -> str:
        selected.append("primary")
        return "primary"

    async def fallback() -> str:
        selected.append("fallback")
        return "fallback"

    async def program(_: RunContext) -> None:
        result = await flow.choice(
            question="pick",
            branches=(("primary", primary), ("fallback", fallback)),
            default_label="fallback",
        )
        assert result == "fallback"

    result = await run(
        program,
        runs_dir=tmp_path,
        runner=runner,
        run_id="choice-default",
        throw_on_error=True,
    )

    assert selected == ["fallback"]
    assert not await anyio.Path(
        result.run_dir,
        "bindings",
        "evaluate.__evaluator__.md",
    ).exists()
    graph = json.loads(await anyio.Path(result.run_dir, "execution-graph.json").read_text())
    choice_trace = graph["root"]["children"][0]
    assert choice_trace["kind"] == "choice"
    assert choice_trace["metadata"]["selected_index"] == 1


@pytest.mark.anyio
async def test_exec_uses_argv_and_captures_stdin_stdout_stderr(tmp_path) -> None:
    observed: list[ExecResult] = []
    literal = "literal & echo shell-was-used"
    script = (
        "import sys;"
        "data=sys.stdin.buffer.read().decode();"
        "sys.stdout.buffer.write((sys.argv[1] + '|' + data + '\\n').encode());"
        "sys.stderr.buffer.write(b'warning\\n')"
    )

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.exec(
                "exec-success",
                (sys.executable, "-c", script, literal),
                stdin="payload",
                binding_name="exec-success",
            )
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="exec-success",
        throw_on_error=True,
    )

    exec_result = observed[0]
    assert exec_result.stdout == f"{literal}|payload"
    assert exec_result.raw.endswith("\n")
    assert exec_result.stderr == "warning\n"
    assert exec_result.exit_code == 0
    assert exec_result.truncated is False
    assert (
        await anyio.Path(
            result.run_dir,
            "bindings",
            "exec-success.md",
        ).read_text()
        == f"{literal}|payload"
    )
    graph = json.loads(await anyio.Path(result.run_dir, "execution-graph.json").read_text())
    trace = graph["root"]["children"][0]
    assert trace["kind"] == "exec"
    assert trace["metadata"]["argv"][-1] == literal


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch files use cmd.exe")
@pytest.mark.parametrize(
    "literal",
    (
        "literal&whoami",
        "literal %USERNAME%! value (100%) &whoami | marker <input> >output",
        "literal ^caret^ and trailing\\",
    ),
)
async def test_exec_escapes_windows_batch_arguments(tmp_path, literal: str) -> None:
    script_dir = anyio.Path(tmp_path, "%USERNAME% & batch")
    await script_dir.mkdir()
    script = script_dir / "echo-arg.cmd"
    await script.write_text(
        "@echo off\r\n"
        "setlocal DisableDelayedExpansion\r\n"
        'set "PSI_AGENT_TEST_ARG=%~1"\r\n'
        f'"{sys.executable}" -c "import os;print(os.environ[\'PSI_AGENT_TEST_ARG\'])"\r\n',
        encoding="utf-8",
    )
    observed: list[ExecResult] = []

    async def program(_: RunContext) -> None:
        observed.append(await flow.exec("batch-argument", (str(script), literal)))

    await run(
        program,
        runs_dir=tmp_path,
        run_id="batch-argument",
        throw_on_error=True,
    )

    assert observed[0].stdout == literal
    assert getpass.getuser().casefold() not in observed[0].stdout.casefold()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch files use cmd.exe")
@pytest.mark.parametrize("literal", ('unsafe"&whoami', "unsafe\r\n&whoami"))
async def test_exec_rejects_unsafe_windows_batch_argument(tmp_path, literal: str) -> None:
    script = anyio.Path(tmp_path, "echo-arg.cmd")
    await script.write_text("@echo off\r\n")

    async def program(_: RunContext) -> None:
        await flow.exec("batch-argument", (str(script), literal))

    with pytest.raises(ValueError, match=r"double quotes.*line breaks"):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="quoted-batch-argument",
            throw_on_error=True,
        )


@pytest.mark.anyio
async def test_exec_nonzero_exit_does_not_commit_binding(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        await flow.exec(
            "exec-failed",
            (
                sys.executable,
                "-c",
                "import sys; print('bad', file=sys.stderr); sys.exit(7)",
            ),
            binding_name="exec-failed",
        )

    with pytest.raises(RuntimeError, match=r"code 7.*bad"):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="exec-nonzero",
            throw_on_error=True,
        )

    assert not await anyio.Path(
        tmp_path,
        "exec-nonzero",
        "bindings",
        "exec-failed.md",
    ).exists()


@pytest.mark.anyio
async def test_exec_timeout_terminates_process_without_binding(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        await flow.exec(
            "exec-timeout",
            (sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_seconds=0.05,
            binding_name="exec-timeout",
        )

    with pytest.raises(TimeoutError, match="timed out"):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="exec-timeout",
            throw_on_error=True,
        )

    assert not await anyio.Path(
        tmp_path,
        "exec-timeout",
        "bindings",
        "exec-timeout.md",
    ).exists()


@pytest.mark.anyio
async def test_exec_cancellation_terminates_process_and_persists_run(tmp_path) -> None:
    marker = anyio.Path(tmp_path, "child-started")
    marker_text = str(marker)
    script = "import sys,time;open(sys.argv[1], 'w', encoding='utf-8').write('started');time.sleep(30)"

    async def program(_: RunContext) -> None:
        await flow.exec(
            "exec-cancelled",
            (sys.executable, "-c", script, marker_text),
            binding_name="exec-cancelled",
        )

    async def invoke() -> None:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="exec-cancelled",
            throw_on_error=True,
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        for _ in range(500):
            if await marker.exists():
                break
            await anyio.sleep(0.01)
        else:
            pytest.fail("child process did not start")
        task_group.cancel_scope.cancel()

    run_dir = anyio.Path(tmp_path, "exec-cancelled")
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["status"] == "cancelled"
    assert not await anyio.Path(
        run_dir,
        "bindings",
        "exec-cancelled.md",
    ).exists()


@pytest.mark.anyio
async def test_exec_kills_immediately_at_output_limit_and_marks_binding(
    tmp_path,
) -> None:
    observed: list[ExecResult] = []
    script = "import sys;chunk=b'x'*4096;\nwhile True:\n sys.stdout.buffer.write(chunk);\n sys.stdout.buffer.flush()"

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.exec(
                "exec-truncated",
                (sys.executable, "-c", script),
                output_limit=32,
                binding_name="exec-truncated",
            )
        )

    with anyio.fail_after(5):
        result = await run(
            program,
            runs_dir=tmp_path,
            run_id="exec-truncated",
            throw_on_error=True,
        )

    exec_result = observed[0]
    assert exec_result.raw == "x" * 32
    assert exec_result.truncated is True
    binding = await anyio.Path(
        result.run_dir,
        "bindings",
        "exec-truncated.md",
    ).read_text()
    assert binding.startswith(
        f"{'x' * 32}\n\n... [truncated at 32 bytes by flow.exec output_limit;",
    )


@pytest.mark.anyio
async def test_exec_consumes_output_before_sending_all_stdin(tmp_path) -> None:
    observed: list[ExecResult] = []
    size = 256 * 1024
    script = (
        "import sys;"
        f"sys.stdout.buffer.write(b'x'*{size});"
        "sys.stdout.buffer.flush();"
        "data=sys.stdin.buffer.read();"
        "sys.stdout.buffer.write(str(len(data)).encode())"
    )

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.exec(
                "exec-duplex",
                (sys.executable, "-c", script),
                stdin=b"y" * size,
                output_limit=size * 2,
            )
        )

    with anyio.fail_after(5):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="exec-duplex",
            throw_on_error=True,
        )

    assert observed[0].raw.endswith(str(size))


@pytest.mark.anyio
@pytest.mark.parametrize("output_limit", [0, math.inf])
async def test_exec_zero_or_infinity_disables_output_limit(
    tmp_path,
    output_limit: int | float,
) -> None:
    observed: list[ExecResult] = []

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.exec(
                "exec-unlimited",
                (sys.executable, "-c", "print('x' * 64)"),
                output_limit=output_limit,
            )
        )

    await run(
        program,
        runs_dir=tmp_path,
        run_id=f"exec-unlimited-{output_limit}",
        throw_on_error=True,
    )

    assert observed[0].stdout == "x" * 64
    assert observed[0].truncated is False


@pytest.mark.anyio
async def test_exec_output_limit_does_not_truncate_stderr(tmp_path) -> None:
    observed: list[ExecResult] = []
    script = "import sys;sys.stderr.write('y' * 256);print('ok')"

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.exec(
                "exec-stderr",
                (sys.executable, "-c", script),
                output_limit=32,
            )
        )

    await run(
        program,
        runs_dir=tmp_path,
        run_id="exec-stderr",
        throw_on_error=True,
    )

    assert observed[0].stderr == "y" * 256
    assert observed[0].truncated is False


@pytest.mark.anyio
async def test_resume_reruns_and_replaces_evaluate_and_exec_bindings(tmp_path) -> None:
    responses = iter(('{"value": true}', '{"value": false}'))
    evaluator_calls = 0
    observed: list[tuple[bool, str]] = []
    counter_path = anyio.Path(tmp_path, "exec-count.txt")

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal evaluator_calls
        evaluator_calls += 1
        return next(responses)

    script = (
        "import os,sys;"
        "p=sys.argv[1];"
        "n=int(open(p).read())+1 if os.path.exists(p) else 1;"
        "open(p,'w').write(str(n));"
        "print(n)"
    )

    async def program(_: RunContext) -> None:
        decision = await flow.evaluate(question="continue?", kind="boolean")
        executed = await flow.exec(
            "side-effect",
            (sys.executable, "-c", script, str(counter_path)),
        )
        observed.append((cast("bool", decision), executed.stdout))

    await run(
        program,
        runs_dir=tmp_path,
        run_id="resume-effects",
        runner=runner,
        throw_on_error=True,
    )
    result = await run(
        program,
        runs_dir=tmp_path,
        resume_from_run_id="resume-effects",
        runner=runner,
        throw_on_error=True,
    )

    assert evaluator_calls == 2
    assert observed == [(True, "1"), (False, "2")]
    assert await counter_path.read_text() == "2"
    bindings = anyio.Path(result.run_dir, "bindings")
    for name in ("evaluate.__evaluator__.md", "side-effect.md"):
        assert await anyio.Path(bindings, name).exists()
    assert not await anyio.Path(bindings, "evaluate.__evaluator__.2.md").exists()
    assert not await anyio.Path(bindings, "side-effect.2.md").exists()
