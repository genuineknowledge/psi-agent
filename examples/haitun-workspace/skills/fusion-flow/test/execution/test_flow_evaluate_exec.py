from __future__ import annotations

import getpass
import json
import math
import re
import sys
import time
from collections.abc import Callable
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
from fusion_flow.execution.flow import flow
from fusion_flow.execution.flow import logger as flow_logger
from fusion_flow.execution.model import (
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
from fusion_flow.execution.runtime import RunContext, run

flow_module = cast("Any", import_module("fusion_flow.execution.flow"))


def test_config_payload_uses_only_system_prompt() -> None:
    payload = flow_module._config_payload(
        AgentConfig(name="judge", system_prompt="Judge."),
    )

    assert payload["system_prompt"] == "Judge."
    assert "system" not in payload
    assert "prompt" not in payload


@pytest.mark.anyio
async def test_custom_evaluator_uses_evaluate_defaults_but_preserves_explicit_values(
    tmp_path,
) -> None:
    received: list[AgentConfig] = []

    async def runner(config: AgentConfig, _: AgentInvocation) -> str:
        received.append(config)
        return '{"value": true}'

    implicit = flow.agent(AgentConfig(name="implicit", system_prompt="Judge."))
    explicit = flow.agent(
        AgentConfig(
            name="explicit",
            system_prompt="Judge.",
            max_tokens=8192,
            temperature=1.0,
            thinking_budget_tokens=1024,
            tools=("search",),
            max_turns=4,
        )
    )

    async def program(_: RunContext) -> None:
        assert await flow.evaluate(
            question="first?",
            kind="boolean",
            agent=implicit,
        )
        assert await flow.evaluate(
            question="second?",
            kind="boolean",
            agent=explicit,
        )

    await run(
        program,
        runs_dir=tmp_path,
        run_id="evaluator-defaults",
        runner=runner,
        throw_on_error=True,
    )

    assert [(config.max_tokens, config.temperature) for config in received] == [
        (256, 0),
        (8192, 1.0),
    ]
    assert [(config.thinking_budget_tokens, config.tools, config.max_turns) for config in received] == [
        (None, (), None),
        (None, (), None),
    ]


@pytest.mark.anyio
async def test_default_evaluator_input_and_bindings_match_ts_text(tmp_path) -> None:
    received: list[tuple[str | None, AgentInvocation]] = []

    async def runner(config: AgentConfig, invocation: AgentInvocation) -> str:
        received.append((config.system_prompt, invocation))
        return '{"value": 3}'

    async def program(_: RunContext) -> None:
        assert (
            await flow.evaluate(
                question="得分?",
                kind="number",
                context={"topic": "海豚"},
                minimum=1,
                maximum=9,
                integer=True,
                binding_name="dynamic-result",
            )
            == 3
        )
        assert await flow.evaluate_static(
            question="same?",
            rule=EqualsRule(expected="海豚", on="海豚"),
            binding_name="static-result",
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        runner=runner,
        run_id="evaluator-ts-text",
        throw_on_error=True,
    )

    assert received == [
        (
            """你是一个严谨的结构化判断器。

你只输出 JSON\uff0c不要任何解释、前后缀、Markdown 代码块。

根据用户给的 `kind` 字段\uff0c输出对应格式\uff1a

- kind = "boolean"\uff1a输出 {"value": true} 或 {"value": false}
- kind = "number"\uff1a输出 {"value": <number>}\uff0c必须是数字字面量
- kind = "choice"\uff1a输出 {"value": "<候选项原文>"}\uff0cvalue 必须严格等于 options 中的某一项

如果信息不足以判断\uff0c按你的最佳推测给出 value\uff0c但保持 JSON 格式。
绝对不要输出额外字段。""",
            AgentInvocation(
                prompt="""# 任务
得分?

# 上下文
## context.topic
海豚

# 输出格式
kind = "number"\uff0c输出 {"value": <number>}\uff08min=1\uff0cmax=9\uff0c必须为整数\uff09。""",
                context={"topic": "海豚"},
            ),
        )
    ]
    bindings = anyio.Path(result.run_dir, "bindings")
    assert await anyio.Path(bindings, "dynamic-result.md").read_text() == ('{\n  "value": 3\n}')
    assert await anyio.Path(bindings, "static-result.md").read_text() == ('{\n  "value": true,\n  "rule": "equals"\n}')


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
    assert [child["metadata"]["raw_answer"] for child in graph["root"]["children"]] == [
        '```json\n{"value": true}\n```',
        '{"value": "9.6"}',
        '{"value": " BLUE "}',
    ]
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
    with pytest.raises(TypeError, match="minimum"):
        await flow.evaluate(
            question="q",
            kind="number",
            minimum=cast("float", True),
        )
    with pytest.raises(ValueError, match="maximum"):
        await flow.evaluate(
            question="q",
            kind="number",
            maximum=math.inf,
        )
    with pytest.raises(ValueError, match="minimum"):
        await flow.evaluate(
            question="q",
            kind="number",
            minimum=math.nan,
        )
    with pytest.raises(ValueError, match="unique"):
        await flow.evaluate(
            question="q",
            kind="choice",
            choices=("YES", "yes"),
        )
    with pytest.raises(TypeError, match="integer"):
        await flow.evaluate(
            question="q",
            kind="number",
            integer=cast("bool", 1),
        )


@pytest.mark.anyio
async def test_failed_evaluate_does_not_commit_a_binding(tmp_path) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> SessionResult:
        return SessionResult(
            '{"value": "not-a-bool"}',
            input_tokens=7,
            output_tokens=3,
        )

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
    graph = json.loads(
        await anyio.Path(
            tmp_path,
            "invalid-evaluate",
            "execution-graph.json",
        ).read_text()
    )
    trace = graph["root"]["children"][0]
    assert trace["tokens"] == {"calls": 1, "input": 7, "output": 3}
    assert trace["metadata"]["raw_answer"] == '{"value": "not-a-bool"}'


@pytest.mark.anyio
async def test_evaluate_rejects_fenced_json_with_surrounding_text(tmp_path) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return 'prefix\n```json\n{"value": true}\n```\nsuffix'

    async def program(_: RunContext) -> None:
        await flow.evaluate(question="boolean?", kind="boolean")

    with pytest.raises(ValueError, match=r"raw=.*prefix"):
        await run(
            program,
            runs_dir=tmp_path,
            runner=runner,
            run_id="surrounded-fenced-json",
            throw_on_error=True,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ("", "not JSON", '{"answer": true}'))
async def test_evaluate_invalid_json_reports_raw_summary(tmp_path, raw: str) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return raw

    async def program(_: RunContext) -> None:
        await flow.evaluate(question="boolean?", kind="boolean")

    with pytest.raises(ValueError, match="raw"):
        await run(
            program,
            runs_dir=tmp_path,
            runner=runner,
            run_id=f"invalid-json-{len(raw)}",
            throw_on_error=True,
        )


@pytest.mark.anyio
async def test_choice_matching_does_not_use_unicode_casefold(tmp_path) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return '{"value": "STRASSE"}'

    async def program(_: RunContext) -> None:
        await flow.evaluate(
            question="choice?",
            kind="choice",
            choices=("straße",),
        )

    with pytest.raises(ValueError, match="allowed values"):
        await run(
            program,
            runs_dir=tmp_path,
            runner=runner,
            run_id="choice-lower-not-casefold",
            throw_on_error=True,
        )


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
async def test_evaluate_static_uses_python_regex_semantics(tmp_path) -> None:
    observed: list[bool] = []

    async def program(_: RunContext) -> None:
        observed.extend(
            (
                await flow.evaluate_static(
                    question="Python string pattern",
                    rule=RegexRule(pattern=r"^\w+$", on="中文"),
                ),
                await flow.evaluate_static(
                    question="Caller-selected ASCII mode",
                    rule=RegexRule(
                        pattern=re.compile(r"^\w+$", re.ASCII),
                        on="中文",
                    ),
                ),
            )
        )

    await run(
        program,
        runs_dir=tmp_path,
        run_id="static-regex-python",
        throw_on_error=True,
    )

    assert observed == [True, False]


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
async def test_choice_uses_default_only_when_evaluation_fails(
    tmp_path,
    monkeypatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(flow_logger, "warning", warnings.append)

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
    fallback_warnings = [warning for warning in warnings if "using default" in warning]
    assert len(fallback_warnings) == 1
    assert "using default 'fallback'" in fallback_warnings[0]
    assert "valid JSON" in fallback_warnings[0]


@pytest.mark.anyio
async def test_choice_rejects_case_insensitive_duplicate_labels() -> None:
    async def branch() -> str:
        return "unused"

    with pytest.raises(ValueError, match="unique"):
        await flow.choice(
            question="pick",
            branches=(("YES", branch), ("yes", branch)),
        )


@pytest.mark.anyio
async def test_explicit_bindings_advance_default_evaluate_and_exec_ordinals(
    tmp_path,
) -> None:
    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        return '{"value": true}'

    async def program(_: RunContext) -> None:
        await flow.evaluate(
            question="first dynamic",
            kind="boolean",
            binding_name="custom-evaluate",
        )
        await flow.evaluate(question="second dynamic", kind="boolean")
        await flow.evaluate_static(
            question="first static",
            rule=ContainsRule(needle="a", on="a"),
            binding_name="custom-static",
        )
        await flow.evaluate_static(
            question="second static",
            rule=ContainsRule(needle="b", on="b"),
        )
        await flow.exec(
            "sequence-command",
            (sys.executable, "-c", "print('first')"),
            binding_name="custom-exec",
        )
        await flow.exec(
            "sequence-command",
            (sys.executable, "-c", "print('second')"),
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        runner=runner,
        run_id="explicit-binding-ordinals",
        throw_on_error=True,
    )
    bindings = anyio.Path(result.run_dir, "bindings")

    for name in (
        "custom-evaluate",
        "evaluate.__evaluator__.2",
        "custom-static",
        "evaluate.static.2",
        "custom-exec",
        "sequence-command.2",
    ):
        assert await anyio.Path(bindings, f"{name}.md").exists()
    for name in (
        "evaluate.__evaluator__",
        "evaluate.static",
        "sequence-command",
    ):
        assert not await anyio.Path(bindings, f"{name}.md").exists()


@pytest.mark.anyio
async def test_session_and_evaluate_share_agent_call_ordinals(tmp_path) -> None:
    judge = flow.agent(AgentConfig(name="judge", system_prompt="Judge."))
    reverse = flow.agent(AgentConfig(name="reverse", system_prompt="Judge."))
    static = flow.agent(AgentConfig(name="__static__", system_prompt="Answer."))

    async def runner(config: AgentConfig, invocation: AgentInvocation) -> str:
        if invocation.prompt in {"session", "static session"}:
            return invocation.prompt
        assert config.name in {"judge", "reverse"}
        return '{"value": true}'

    async def program(_: RunContext) -> None:
        await flow.session(judge, "session")
        await flow.evaluate(question="judge?", kind="boolean", agent=judge)
        await flow.evaluate(question="reverse?", kind="boolean", agent=reverse)
        await flow.session(reverse, "session")
        await flow.session(static, "static session")
        await flow.evaluate_static(
            question="static?",
            rule=ContainsRule(needle="yes", on="yes"),
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        runner=runner,
        run_id="shared-evaluator-ordinals",
        throw_on_error=True,
    )
    bindings = anyio.Path(result.run_dir, "bindings")

    for name in (
        "judge",
        "evaluate.judge.2",
        "evaluate.reverse",
        "reverse.2",
        "__static__",
        "evaluate.static.2",
    ):
        assert await anyio.Path(bindings, f"{name}.md").exists()
    for name in ("evaluate.judge", "reverse", "evaluate.static"):
        assert not await anyio.Path(bindings, f"{name}.md").exists()
    meta = json.loads(
        await anyio.Path(result.run_dir, "meta.json").read_text(),
    )
    assert meta["session_calls"] == {"judge": 2, "reverse": 2}
    assert meta["evaluator_calls"] == {"__static__": 2}
    graph = json.loads(
        await anyio.Path(result.run_dir, "execution-graph.json").read_text(),
    )
    nodes = graph["root"]["children"]
    assert nodes[0]["metadata"]["binding_name"] == "judge"
    assert nodes[0]["metadata"]["trace_file"] == "trace/judge.json"
    assert nodes[1]["metadata"]["binding_name"] == "evaluate.judge.2"
    assert nodes[1]["metadata"]["trace_file"] == "trace/evaluate.judge.2.json"
    assert nodes[5]["metadata"]["binding_name"] == "evaluate.static.2"
    assert nodes[5]["metadata"]["evaluator_agent"] == "__static__"


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
    assert trace["metadata"] == {
        "name": "exec-success",
        "command": " ".join((sys.executable, "-c", script, literal))[:200],
        "binding_name": "exec-success",
        "exit_code": 0,
        "truncated": False,
    }
    metadata = json.loads(
        await anyio.Path(
            result.run_dir,
            "bindings",
            "exec-success.meta.json",
        ).read_text()
    )
    assert metadata["produced_by"] == "exec:exec-success"


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch files use cmd.exe")
@pytest.mark.parametrize(
    "literal",
    (
        "literal&whoami",
        "literal %USERNAME% value (100%) &whoami | marker <input> >output",
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
@pytest.mark.parametrize(
    "literal",
    ('unsafe"&whoami', "unsafe!value", "unsafe\r\n&whoami"),
)
async def test_exec_rejects_unsafe_windows_batch_argument(tmp_path, literal: str) -> None:
    script = anyio.Path(tmp_path, "echo-arg.cmd")
    await script.write_text("@echo off\r\n")

    async def program(_: RunContext) -> None:
        await flow.exec("batch-argument", (str(script), literal))

    with pytest.raises(ValueError, match="Windows batch argv"):
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
    graph = json.loads(
        await anyio.Path(
            tmp_path,
            "exec-nonzero",
            "execution-graph.json",
        ).read_text()
    )
    assert graph["root"]["children"][0]["metadata"]["exit_code"] == 7
    assert graph["root"]["children"][0]["metadata"]["truncated"] is False


@pytest.mark.anyio
async def test_exec_nonzero_error_only_includes_output_tail(tmp_path) -> None:
    prefix = "a" * 400
    suffix = "TAIL"

    async def program(_: RunContext) -> None:
        await flow.exec(
            "exec-tail",
            (
                sys.executable,
                "-c",
                f"import sys;sys.stderr.write({prefix + suffix!r});sys.exit(9)",
            ),
        )

    with pytest.raises(RuntimeError) as raised:
        await run(
            program,
            runs_dir=tmp_path,
            run_id="exec-tail",
            throw_on_error=True,
        )

    assert suffix in str(raised.value)
    assert prefix not in str(raised.value)


@pytest.mark.anyio
async def test_exec_validates_argv_container_and_timeout(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        with pytest.raises(TypeError, match="argv"):
            await flow.exec("string-argv", cast("tuple[str, ...]", "abc"))
        with pytest.raises(TypeError, match="argv"):
            await flow.exec("bytes-argv", cast("tuple[str, ...]", b"abc"))
        for timeout in (True, 0, math.inf, math.nan, "1"):
            with pytest.raises((TypeError, ValueError), match="timeout_seconds"):
                await flow.exec(
                    "bad-timeout",
                    (sys.executable, "-c", "pass"),
                    timeout_seconds=cast("float", timeout),
                )

    await run(
        program,
        runs_dir=tmp_path,
        run_id="exec-validation",
        throw_on_error=True,
    )


@pytest.mark.anyio
async def test_read_process_streams_keeps_output_arriving_after_wait() -> None:
    exited = anyio.Event()

    class TailStream:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def receive(self) -> bytes:
            await exited.wait()
            if not self.payload:
                raise anyio.EndOfStream
            payload, self.payload = self.payload, b""
            return payload

    class Process:
        def __init__(self) -> None:
            self.stdout = TailStream(b"stdout-tail")
            self.stderr = TailStream(b"stderr-tail")
            self.stdin = None

        async def wait(self) -> int:
            exited.set()
            return 0

    stderr_tail = bytearray()
    observed = await flow_module._read_process_streams(
        Process(),
        stdin_payload=None,
        output_limit=None,
        stderr_tail=stderr_tail,
    )

    assert observed == (b"stdout-tail", False, b"stderr-tail", 0)
    assert stderr_tail == b"stderr-tail"


@pytest.mark.anyio
async def test_read_process_streams_ignores_broken_pipe_from_stdin() -> None:
    class EmptyStream:
        async def receive(self) -> bytes:
            raise anyio.EndOfStream

    class BrokenPipeStdin:
        def __init__(self) -> None:
            self.closed = False
            self.payloads: list[bytes] = []

        async def send(self, payload: bytes) -> None:
            self.payloads.append(payload)
            raise BrokenPipeError

        async def aclose(self) -> None:
            self.closed = True

    class Process:
        def __init__(self) -> None:
            self.stdout = EmptyStream()
            self.stderr = EmptyStream()
            self.stdin = BrokenPipeStdin()

        async def wait(self) -> int:
            return 0

    process = Process()
    stderr_tail = bytearray()

    observed = await flow_module._read_process_streams(
        process,
        stdin_payload=b"payload",
        output_limit=None,
        stderr_tail=stderr_tail,
    )

    assert observed == (b"", False, b"", 0)
    assert process.stdin.payloads == [b"payload"]
    assert process.stdin.closed is True
    assert stderr_tail == b""


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows process tree fallback")
async def test_terminate_process_falls_back_when_job_termination_fails(
    monkeypatch,
) -> None:
    job = object()
    closed: list[object] = []
    taskkill_calls: list[tuple[str, ...]] = []

    def terminate_job(_: object, __: int) -> bool:
        return False

    def close_handle(value: object) -> bool:
        closed.append(value)
        return True

    async def run_process(command: tuple[str, ...], *, check: bool) -> object:
        assert check is False
        taskkill_calls.append(command)
        return object()

    class Process:
        def __init__(self) -> None:
            self._psi_agent_job = job
            self.returncode: int | None = None
            self.pid = 123

        def kill(self) -> None:
            self.returncode = -1

        async def wait(self) -> int:
            return self.returncode or 0

    monkeypatch.setattr(
        flow_module,
        "_kernel32",
        SimpleNamespace(
            TerminateJobObject=terminate_job,
            CloseHandle=close_handle,
        ),
    )
    monkeypatch.setattr(anyio, "run_process", run_process)

    await flow_module._terminate_process(Process())

    assert taskkill_calls == [("taskkill", "/PID", "123", "/T", "/F")]
    assert closed == [job]


@pytest.mark.anyio
async def test_exec_timeout_terminates_process_without_binding(tmp_path) -> None:
    async def program(_: RunContext) -> None:
        await flow.exec(
            "exec-timeout",
            (
                sys.executable,
                "-c",
                "import sys,time;print('READY',file=sys.stderr,flush=True);time.sleep(30)",
            ),
            timeout_seconds=0.3,
            binding_name="exec-timeout",
        )

    with pytest.raises(TimeoutError, match=r"timed out.*READY"):
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
@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch files use cmd.exe")
async def test_exec_timeout_terminates_windows_batch_process_tree(tmp_path) -> None:
    child = anyio.Path(tmp_path, "child.py")
    batch = anyio.Path(tmp_path, "launch.cmd")
    started = anyio.Path(tmp_path, "started")
    survived = anyio.Path(tmp_path, "survived")
    await child.write_text(
        "import pathlib,sys,time\n"
        "pathlib.Path(sys.argv[1]).write_text('started', encoding='utf-8')\n"
        "time.sleep(2)\n"
        "pathlib.Path(sys.argv[2]).write_text('survived', encoding='utf-8')\n",
        encoding="utf-8",
    )
    await batch.write_text(
        f'@echo off\r\n"{sys.executable}" "{child}" "{started}" "{survived}"\r\n',
        encoding="utf-8",
    )

    async def program(_: RunContext) -> None:
        await flow.exec(
            "batch-timeout",
            (str(batch),),
            timeout_seconds=0.8,
        )

    before = time.perf_counter()
    with pytest.raises(TimeoutError):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="batch-timeout",
            throw_on_error=True,
        )
    elapsed = time.perf_counter() - before

    assert elapsed < 1.5
    assert await started.exists()
    await anyio.sleep(2)
    assert not await survived.exists()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch files use cmd.exe")
async def test_exec_timeout_kills_batch_grandchild_after_wrapper_exits(tmp_path) -> None:
    child = anyio.Path(tmp_path, "grandchild.py")
    batch = anyio.Path(tmp_path, "launch-grandchild.cmd")
    started = anyio.Path(tmp_path, "grandchild-started")
    survived = anyio.Path(tmp_path, "grandchild-survived")
    await child.write_text(
        "import pathlib,signal,sys,time\n"
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text('started', encoding='utf-8')\n"
        "time.sleep(2)\n"
        "pathlib.Path(sys.argv[2]).write_text('survived', encoding='utf-8')\n",
        encoding="utf-8",
    )
    await batch.write_text(
        f'@echo off\r\nstart "" /b "{sys.executable}" "{child}" "{started}" "{survived}"\r\necho launched\r\n',
        encoding="utf-8",
    )

    async def program(_: RunContext) -> None:
        await flow.exec(
            "batch-grandchild-timeout",
            (str(batch),),
            timeout_seconds=0.8,
        )

    with pytest.raises(TimeoutError):
        await run(
            program,
            runs_dir=tmp_path,
            run_id="batch-grandchild-timeout",
            throw_on_error=True,
        )

    assert await started.exists()
    await anyio.sleep(2.2)
    assert not await survived.exists()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch files use cmd.exe")
async def test_exec_success_does_not_kill_detached_batch_child(tmp_path) -> None:
    child = anyio.Path(tmp_path, "detached-child.py")
    batch = anyio.Path(tmp_path, "launch-detached.cmd")
    survived = anyio.Path(tmp_path, "detached-survived")
    observed: list[ExecResult] = []
    await child.write_text(
        "import pathlib,sys,time\n"
        "time.sleep(0.5)\n"
        "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')\n",
        encoding="utf-8",
    )
    await batch.write_text(
        f'@echo off\r\nstart "" /b "{sys.executable}" "{child}" "{survived}" >NUL 2>&1\r\necho wrapper-done\r\n',
        encoding="utf-8",
    )

    async def program(_: RunContext) -> None:
        observed.append(await flow.exec("batch-detached-child", (str(batch),)))

    await run(
        program,
        runs_dir=tmp_path,
        run_id="batch-detached-child",
        throw_on_error=True,
    )

    assert observed[0].stdout == "wrapper-done"
    await anyio.sleep(1)
    assert await survived.exists()


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
async def test_exec_artifacts_only_keep_a_bounded_command_preview(tmp_path) -> None:
    secret = "TOKEN-should-not-be-persisted"
    padding = "x" * 300

    async def program(_: RunContext) -> None:
        await flow.exec(
            "exec-artifacts",
            (
                sys.executable,
                "-c",
                "print('y' * 1000)",
                padding + secret,
            ),
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="exec-artifacts",
        throw_on_error=True,
    )

    graph_text = await anyio.Path(result.run_dir, "execution-graph.json").read_text()
    graph = json.loads(graph_text)
    trace = graph["root"]["children"][0]
    binding_meta_text = await anyio.Path(
        result.run_dir,
        "bindings",
        "exec-artifacts.meta.json",
    ).read_text()
    assert secret not in graph_text
    assert secret not in binding_meta_text
    assert len(trace["metadata"]["command"]) <= 200
    assert trace["output_summary"] is None


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
async def test_exec_preserves_success_exit_code_when_output_is_truncated(
    tmp_path,
    monkeypatch,
) -> None:
    async def open_process(*_: object, **__: object) -> SimpleNamespace:
        return SimpleNamespace(pid=1, returncode=0)

    async def read_process_streams(*_: object, **__: object) -> tuple[bytes, bool, bytes, int]:
        return b"prefix", True, b"", 0

    monkeypatch.setattr(anyio, "open_process", open_process)
    monkeypatch.setattr(flow_module, "_read_process_streams", read_process_streams)
    observed: list[ExecResult] = []

    async def program(_: RunContext) -> None:
        observed.append(
            await flow.exec(
                "exec-truncated-success",
                (sys.executable, "-c", "pass"),
                output_limit=6,
            )
        )

    await run(
        program,
        runs_dir=tmp_path,
        run_id="exec-truncated-success",
        throw_on_error=True,
    )

    assert observed[0].exit_code == 0
    assert observed[0].truncated is True


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
