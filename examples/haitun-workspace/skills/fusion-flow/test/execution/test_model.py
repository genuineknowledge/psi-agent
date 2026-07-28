from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from fusion_flow_next.execution.model import (
    AgentConfig,
    AgentHandle,
    AgentInvocation,
    BlockHandle,
    ExecResult,
    ExecutionTrace,
    RunResult,
    ServiceHandle,
    ServiceParam,
    SessionResult,
    SessionRunner,
    TokenSummary,
    TokenUsage,
    aggregate_tokens,
    assert_safe_name,
    format_token_count,
)


def test_format_token_count_matches_typescript_thresholds() -> None:
    assert format_token_count(999) == "999"
    assert format_token_count(1_000) == "1.0k"
    assert format_token_count(1_150) == "1.1k"
    assert format_token_count(1_250) == "1.3k"
    assert format_token_count(1_000_000) == "1.00M"
    assert format_token_count(1_005_000) == "1.00M"
    assert format_token_count(1_125_000) == "1.13M"
    assert format_token_count(9_007_199_254_744_999) == "9007199254.75M"


def test_format_token_count_distinguishes_unknown_from_zero() -> None:
    assert format_token_count(None) == "unknown"
    assert format_token_count(0) == "0"


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", ".", "..", "name. ", ""])
def test_assert_safe_name_rejects_unsafe_paths(name: str) -> None:
    with pytest.raises(ValueError):
        assert_safe_name(name)


@pytest.mark.parametrize(
    "name",
    ["CON", "con.txt", "PRN", "AUX.json", "NUL", "COM1", "com9.log", "LPT1", "lpt9.txt"],
)
def test_assert_safe_name_rejects_windows_reserved_device_names(name: str) -> None:
    with pytest.raises(ValueError):
        assert_safe_name(name)


@pytest.mark.parametrize("name", ["a:b", 'a"b', "a<b", "a>b", "a|b", "a?b", "a*b"])
def test_assert_safe_name_rejects_other_windows_unsafe_characters(name: str) -> None:
    with pytest.raises(ValueError):
        assert_safe_name(name)


def test_assert_safe_name_normalizes_to_nfc() -> None:
    assert assert_safe_name("cafe\u0301") == "café"
    assert assert_safe_name("中文-name_1.2") == "中文-name_1.2"


@pytest.mark.parametrize("name", ["COM0", "COM10", "LPT0", "LPT10", "CONSOLE"])
def test_assert_safe_name_does_not_overmatch_windows_device_names(name: str) -> None:
    assert assert_safe_name(name) == name


def test_agent_config_accepts_only_system_prompt() -> None:
    config = AgentConfig(name="writer", system_prompt="Write clearly")

    assert config.system_prompt == "Write clearly"
    assert not hasattr(config, "system")
    assert not hasattr(config, "prompt")


@pytest.mark.parametrize("system_prompt", [None, ""])
def test_agent_config_requires_a_non_empty_system_prompt(
    system_prompt: str | None,
) -> None:
    with pytest.raises(ValueError, match="system_prompt"):
        AgentConfig(name="writer", system_prompt=system_prompt)


def test_agent_config_keeps_defaults() -> None:
    config = AgentConfig(name="writer", system_prompt="Primary")

    assert config.system_prompt == "Primary"
    assert config.max_tokens is None
    assert config.temperature is None
    assert config.tools == ()
    assert config.context_schema is None


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "error"),
    [
        (True, 1, TypeError),
        (1.5, 1, TypeError),
        (-1, 1, ValueError),
        (1, False, TypeError),
        (1, -1, ValueError),
    ],
)
def test_session_result_rejects_invalid_token_counts(
    input_tokens: object,
    output_tokens: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        SessionResult(
            text="done",
            input_tokens=cast("int", input_tokens),
            output_tokens=cast("int", output_tokens),
        )


@pytest.mark.parametrize(
    ("calls", "input_tokens", "output_tokens", "error"),
    [
        (True, 1, 1, TypeError),
        (-1, 1, 1, ValueError),
        (1, 1.5, 1, TypeError),
        (1, 1, -1, ValueError),
    ],
)
def test_token_usage_rejects_invalid_counts(
    calls: object,
    input_tokens: object,
    output_tokens: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        TokenUsage(
            calls=cast("int", calls),
            input=cast("int", input_tokens),
            output=cast("int", output_tokens),
        )


def test_public_value_models_have_stable_frozen_shapes() -> None:
    config = AgentConfig(name="writer", system_prompt="Write")
    invocation_context = {"topic": "python"}
    invocation = AgentInvocation(prompt="Draft", context=invocation_context)
    result = SessionResult(text="done", input_tokens=2, output_tokens=1)
    agent = AgentHandle(name="writer", config=config)
    param = ServiceParam(name="topic", description="Subject", required=True)
    service = ServiceHandle(name="research", params=(param,))
    block = BlockHandle(name="draft", description="Draft one answer")
    exec_result = ExecResult(
        stdout="ok",
        raw="ok\n",
        stderr="",
        exit_code=0,
        duration_ms=1.5,
        truncated=False,
    )
    run_result = RunResult(run_id="run-1", run_dir="runs/run-1", status="ok")

    invocation_context["topic"] = "changed"
    assert invocation.context == {"topic": "python"}
    assert agent.kind == "agent"
    assert service.kind == "service"
    assert service.params == (param,)
    assert block.kind == "block"
    assert exec_result.stdout == "ok"
    assert run_result.status == "ok"
    assert result.text == "done"
    with pytest.raises(FrozenInstanceError):
        config.__setattr__("name", "changed")


def test_session_runner_alias_accepts_async_runner() -> None:
    async def runner(
        config: AgentConfig,
        invocation: AgentInvocation,
    ) -> SessionResult:
        return SessionResult(text=f"{config.name}: {invocation.prompt}")

    session_runner: SessionRunner = runner

    assert session_runner is runner


def test_aggregate_tokens_skips_cached_nodes_but_counts_their_children() -> None:
    cached_child = ExecutionTrace(
        trace_id="cached-child",
        kind="session",
        label="cached child",
        started_at="2026-07-23T00:00:00Z",
        status="ok",
        tokens=TokenUsage(calls=1, input=10_000, output=5_000),
    )
    cached_parent = ExecutionTrace(
        trace_id="cached",
        kind="block",
        label="cached",
        started_at="2026-07-23T00:00:00Z",
        status="ok",
        children=(cached_child,),
        tokens=TokenUsage(calls=1, input=1_000, output=500),
        cached=True,
    )
    root = ExecutionTrace(
        trace_id="root",
        kind="run",
        label="run-1",
        started_at="2026-07-23T00:00:00Z",
        children=(
            ExecutionTrace(
                trace_id="session",
                kind="session",
                label="writer",
                started_at="2026-07-23T00:00:00Z",
                status="ok",
                tokens=TokenUsage(calls=1, input=20, output=7),
                metadata={"agent": "writer"},
            ),
            ExecutionTrace(
                trace_id="evaluate",
                kind="evaluate",
                label="judge",
                started_at="2026-07-23T00:00:00Z",
                status="ok",
                tokens=TokenUsage(calls=1, input=5, output=1),
                metadata={"evaluator_agent": "__evaluator__"},
            ),
            cached_parent,
        ),
    )

    assert aggregate_tokens(root) == TokenSummary(
        user=TokenUsage(calls=2, input=10_020, output=5_007),
        internal=TokenUsage(calls=1, input=5, output=1),
        calls=3,
        input=10_025,
        output=5_008,
    )


def test_aggregate_tokens_preserves_unknown_counts() -> None:
    root = ExecutionTrace(
        trace_id="root",
        kind="run",
        label="run-1",
        started_at="2026-07-23T00:00:00Z",
        children=(
            ExecutionTrace(
                trace_id="session",
                kind="session",
                label="writer",
                started_at="2026-07-23T00:00:00Z",
                status="ok",
                tokens=TokenUsage(calls=1, input=None, output=3),
                metadata={"agent": "writer"},
            ),
        ),
    )

    assert aggregate_tokens(root) == TokenSummary(
        user=TokenUsage(calls=1, input=None, output=3),
        internal=TokenUsage(calls=0, input=0, output=0),
        calls=1,
        input=None,
        output=3,
    )


def test_aggregate_tokens_does_not_infer_owner_from_label() -> None:
    root = ExecutionTrace(
        trace_id="root",
        kind="run",
        label="run-1",
        started_at="2026-07-23T00:00:00Z",
        children=(
            ExecutionTrace(
                trace_id="unknown-owner",
                kind="session",
                label="__misleading_label__",
                started_at="2026-07-23T00:00:00Z",
                status="ok",
                tokens=TokenUsage(calls=7, input=2, output=1),
            ),
        ),
    )

    assert aggregate_tokens(root).user == TokenUsage(calls=1, input=2, output=1)
    assert aggregate_tokens(root).internal == TokenUsage(calls=0, input=0, output=0)


def test_aggregate_tokens_uses_agent_when_evaluator_owner_is_none() -> None:
    root = ExecutionTrace(
        trace_id="root",
        kind="run",
        label="run-1",
        started_at="2026-07-23T00:00:00Z",
        children=(
            ExecutionTrace(
                trace_id="internal",
                kind="evaluate",
                label="judge",
                started_at="2026-07-23T00:00:00Z",
                status="ok",
                tokens=TokenUsage(calls=1, input=3, output=1),
                metadata={
                    "evaluator_agent": None,
                    "agent": "__internal__",
                },
            ),
        ),
    )

    assert aggregate_tokens(root).user == TokenUsage(calls=0, input=0, output=0)
    assert aggregate_tokens(root).internal == TokenUsage(calls=1, input=3, output=1)


def test_execution_trace_to_dict_is_json_serializable() -> None:
    child = ExecutionTrace(
        trace_id="child",
        kind="session",
        label="writer",
        status="ok",
        started_at="2026-07-23T00:00:00Z",
        finished_at="2026-07-23T00:00:01Z",
        duration_ms=1_000,
        input_summary="Draft",
        output_summary="Done",
        tokens=TokenUsage(calls=1, input=12, output=4),
        metadata={"agent": "writer"},
    )
    root = ExecutionTrace(
        trace_id="root",
        kind="run",
        label="run-1",
        status="ok",
        started_at="2026-07-23T00:00:00Z",
        finished_at="2026-07-23T00:00:01Z",
        duration_ms=1_000,
        children=(child,),
    )

    payload = root.to_dict()
    decoded = json.loads(json.dumps(payload))

    assert decoded["trace_id"] == "root"
    assert decoded["children"][0]["kind"] == "session"
    assert decoded["children"][0]["tokens"] == {
        "calls": 1,
        "input": 12,
        "output": 4,
    }
    assert decoded["children"][0]["metadata"] == {"agent": "writer"}
