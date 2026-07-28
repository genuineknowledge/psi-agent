from __future__ import annotations

import inspect
import json
from typing import cast

import anyio
import pytest
from fusion_flow.execution import (
    Agent,
    AgentConfig,
    AgentInvocation,
    ServiceParam,
    SessionResult,
    flow,
    run,
)
from fusion_flow.execution import runtime as runtime_module
from fusion_flow.execution.runtime import RunContext


def test_agent_returns_a_handle_without_an_active_run() -> None:
    config = AgentConfig(name="writer", system_prompt="Write clearly.")

    handle = flow.agent(config)

    assert handle.name == "writer"
    assert handle.config == config
    assert handle.kind == "agent"


@pytest.mark.anyio
async def test_session_requires_runner_only_when_called(tmp_path) -> None:
    handle = flow.agent(AgentConfig(name="writer", system_prompt="Write clearly."))

    async def program(_: RunContext) -> None:
        with pytest.raises(RuntimeError, match="injected runner"):
            await flow.session(handle, "hello")

    result = await run(program, runs_dir=tmp_path, run_id="missing-runner")

    assert result.status == "ok"


@pytest.mark.anyio
async def test_session_requires_exact_context_schema(tmp_path) -> None:
    calls: list[AgentInvocation] = []

    async def runner(
        _: AgentConfig,
        invocation: AgentInvocation,
    ) -> str:
        calls.append(invocation)
        return "ok"

    handle = flow.agent(
        AgentConfig(
            name="writer",
            system_prompt="Write clearly.",
            context_schema=("topic", "language"),
        )
    )

    async def program(_: RunContext) -> None:
        with pytest.raises(ValueError, match="match exactly"):
            await flow.session(handle, "hello", {"topic": "Python"})
        with pytest.raises(ValueError, match="match exactly"):
            await flow.session(
                handle,
                "hello",
                {
                    "topic": "Python",
                    "language": "English",
                    "typo": "extra",
                },
            )
        assert (
            await flow.session(
                handle,
                "hello",
                {"topic": "Python", "language": "English"},
            )
            == "ok"
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="context-schema",
        runner=runner,
    )

    assert result.status == "ok"
    assert calls == [
        AgentInvocation(
            prompt="hello",
            context={"topic": "Python", "language": "English"},
        )
    ]


@pytest.mark.anyio
async def test_session_applies_defaults_and_ignores_empty_context_schema(
    tmp_path,
) -> None:
    received: list[tuple[AgentConfig, AgentInvocation]] = []

    async def runner(
        config: AgentConfig,
        invocation: AgentInvocation,
    ) -> str:
        received.append((config, invocation))
        return "ok"

    handle = flow.agent(
        AgentConfig(
            name="writer",
            system_prompt="Write clearly.",
            context_schema=(),
        )
    )

    async def program(_: RunContext) -> None:
        assert await flow.session(handle, "hello", {"extra": "allowed"}) == "ok"

    await run(
        program,
        runs_dir=tmp_path,
        run_id="session-defaults",
        runner=runner,
        throw_on_error=True,
    )

    config, invocation = received[0]
    assert config.max_tokens == 8192
    assert config.temperature == 1.0
    assert invocation.context == {"extra": "allowed"}


@pytest.mark.anyio
async def test_session_resume_hash_ignores_tool_order(tmp_path) -> None:
    calls = 0

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        return "cached"

    async def first(_: RunContext) -> None:
        handle = flow.agent(
            AgentConfig(
                name="writer",
                system_prompt="Write clearly.",
                tools=("read", "write"),
            )
        )
        assert await flow.session(handle, "hello") == "cached"

    await run(
        first,
        runs_dir=tmp_path,
        run_id="tool-order",
        runner=runner,
        throw_on_error=True,
    )

    async def resumed(_: RunContext) -> None:
        handle = flow.agent(
            AgentConfig(
                name="writer",
                system_prompt="Write clearly.",
                tools=("write", "read"),
            )
        )
        assert await flow.session(handle, "hello") == "cached"

    await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id="tool-order",
        runner=runner,
        throw_on_error=True,
    )

    assert calls == 1
    meta = json.loads(await anyio.Path(tmp_path, "tool-order", "meta.json").read_text())
    assert meta["session_calls"] == {"writer": 1}
    assert meta["llm_calls"] == 0


@pytest.mark.anyio
async def test_session_failure_does_not_consume_default_binding_number(
    tmp_path,
) -> None:
    calls = 0

    async def runner(
        _: AgentConfig,
        __: AgentInvocation,
    ) -> SessionResult | str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        if calls == 2:
            return "plain"
        return SessionResult(text="rich", input_tokens=11, output_tokens=7)

    handle = flow.agent(AgentConfig(name="writer", system_prompt="Write clearly."))

    async def program(_: RunContext) -> None:
        with pytest.raises(RuntimeError, match="transient"):
            await flow.session(handle, "first")
        assert await flow.session(handle, "second") == "plain"
        assert await flow.session(handle, "third") == "rich"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="session-numbering",
        runner=runner,
    )
    run_dir = anyio.Path(result.run_dir)

    assert await anyio.Path(run_dir, "bindings", "writer.md").read_text() == "plain"
    assert await anyio.Path(run_dir, "bindings", "writer.2.md").read_text() == "rich"
    assert not await anyio.Path(run_dir, "bindings", "writer.3.md").exists()

    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    sessions = graph["root"]["children"]
    assert [node["status"] for node in sessions] == ["error", "ok", "ok"]
    assert sessions[1]["tokens"] == {
        "calls": 1,
        "input": None,
        "output": None,
    }
    assert sessions[2]["tokens"] == {
        "calls": 1,
        "input": 11,
        "output": 7,
    }
    assert sessions[1]["metadata"]["binding_name"] == "writer"
    assert sessions[1]["metadata"]["trace_file"] == "trace/writer.json"
    plain_trace = json.loads(await anyio.Path(run_dir, "trace", "writer.json").read_text())
    rich_trace = json.loads(await anyio.Path(run_dir, "trace", "writer.2.json").read_text())
    assert plain_trace["tokens"] == sessions[1]["tokens"]
    assert rich_trace["tokens"] == sessions[2]["tokens"]
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["session_calls"] == {"writer": 2}


@pytest.mark.anyio
async def test_session_failure_releases_explicit_binding(tmp_path) -> None:
    calls = 0

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return "saved"

    handle = flow.agent(AgentConfig(name="writer", system_prompt="Write clearly."))

    async def program(_: RunContext) -> None:
        with pytest.raises(RuntimeError, match="transient"):
            await flow.session(
                handle,
                "first",
                binding_name="answer",
            )
        assert (
            await flow.session(
                handle,
                "second",
                binding_name="answer",
            )
            == "saved"
        )
        with pytest.raises(ValueError, match="already exists"):
            await flow.session(
                handle,
                "third",
                binding_name="answer",
            )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="explicit-binding",
        runner=runner,
    )

    assert calls == 2
    assert (
        await anyio.Path(
            result.run_dir,
            "bindings",
            "answer.md",
        ).read_text()
        == "saved"
    )


@pytest.mark.anyio
async def test_cancelled_session_releases_default_binding(tmp_path) -> None:
    calls = 0

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            await anyio.sleep_forever()
        return "recovered"

    handle = flow.agent(AgentConfig(name="writer", system_prompt="Write clearly."))

    async def program(_: RunContext) -> None:
        with anyio.move_on_after(0.2) as cancel_scope:
            await flow.session(handle, "cancel me")
        assert cancel_scope.cancel_called
        assert await flow.session(handle, "retry") == "recovered"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="session-cancel",
        runner=runner,
    )
    run_dir = anyio.Path(result.run_dir)

    assert result.status == "ok"
    assert calls == 2
    assert await anyio.Path(run_dir, "bindings", "writer.md").read_text() == "recovered"
    assert not await anyio.Path(run_dir, "bindings", "writer.2.md").exists()
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    assert [node["status"] for node in graph["root"]["children"]] == [
        "cancelled",
        "ok",
    ]


@pytest.mark.anyio
async def test_service_validates_registration_and_declared_parameters(
    tmp_path,
) -> None:
    calls: list[dict[str, str]] = []

    async def body(args: dict[str, str]) -> str:
        calls.append(args)
        return f"{args['query']}:{args.get('language', 'default')}"

    async def program(_: RunContext) -> None:
        with pytest.raises(ValueError, match="duplicate service parameter"):
            flow.service(
                "duplicate",
                body,
                params=(
                    ServiceParam(name="query", required=True),
                    ServiceParam(name="query", required=False),
                ),
            )
        handle = flow.service(
            "lookup",
            body,
            params=(
                ServiceParam(name="query", required=True),
                ServiceParam(name="language", required=False),
            ),
        )
        with pytest.raises(ValueError, match="already defined"):
            flow.service("lookup", body)
        with pytest.raises(ValueError, match="missing required"):
            await flow.call(handle, {"language": "zh"})
        with pytest.raises(ValueError, match="unknown arguments"):
            await flow.call(handle, {"query": "Python", "typo": "extra"})

        assert await flow.call(handle, {"query": "Python"}) == "Python:default"
        assert (
            await flow.use(
                "lookup",
                {"query": "AnyIO", "language": "zh"},
            )
            == "AnyIO:zh"
        )

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="service-call",
        throw_on_error=True,
    )

    assert result.status == "ok"
    assert calls == [
        {"query": "Python"},
        {"query": "AnyIO", "language": "zh"},
    ]
    assert (
        await anyio.Path(
            result.run_dir,
            "bindings",
            "lookup.md",
        ).read_text()
        == "Python:default"
    )
    assert (
        await anyio.Path(
            result.run_dir,
            "bindings",
            "lookup.2.md",
        ).read_text()
        == "AnyIO:zh"
    )
    meta = json.loads(await anyio.Path(result.run_dir, "meta.json").read_text())
    assert meta["service_calls"] == {"lookup": 2}
    graph = json.loads(
        await anyio.Path(result.run_dir, "execution-graph.json").read_text(),
    )
    assert graph["root"]["children"][0]["metadata"] == {
        "service": "lookup",
        "args": {"query": "Python"},
        "binding_name": "lookup",
    }
    assert graph["root"]["children"][1]["metadata"] == {
        "service": "lookup",
        "args": {"query": "AnyIO", "language": "zh"},
        "binding_name": "lookup.2",
    }


@pytest.mark.anyio
async def test_service_resume_hash_preserves_argument_order(tmp_path) -> None:
    calls = 0

    async def body(args: dict[str, str]) -> str:
        nonlocal calls
        calls += 1
        return ",".join(args)

    async def first(_: RunContext) -> None:
        handle = flow.service("ordered", body)
        assert await flow.call(handle, {"a": "1", "b": "2"}) == "a,b"

    await run(
        first,
        runs_dir=tmp_path,
        run_id="argument-order",
        throw_on_error=True,
    )

    async def resumed(_: RunContext) -> None:
        handle = flow.service("ordered", body)
        assert await flow.call(handle, {"b": "2", "a": "1"}) == "b,a"

    await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id="argument-order",
        throw_on_error=True,
    )

    assert calls == 2


@pytest.mark.anyio
async def test_service_body_must_return_a_string_without_committing_binding(
    tmp_path,
) -> None:
    attempts = 0

    async def body(_: dict[str, str]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return cast("str", 42)
        return "usable"

    async def program(_: RunContext) -> None:
        handle = flow.service("lookup", body)
        with pytest.raises(TypeError, match="must return a string"):
            await flow.call(handle)
        assert await flow.call(handle) == "usable"

    result = await run(program, runs_dir=tmp_path, run_id="service-result")

    assert result.status == "ok"
    assert (
        await anyio.Path(
            result.run_dir,
            "bindings",
            "lookup.md",
        ).read_text()
        == "usable"
    )
    assert not await anyio.Path(
        result.run_dir,
        "bindings",
        "lookup.2.md",
    ).exists()


@pytest.mark.anyio
async def test_flow_and_run_context_share_single_assignment_state(tmp_path) -> None:
    async def program(context: RunContext) -> None:
        assert await flow.input("topic", "default") == "override"
        with pytest.raises(ValueError, match="already read"):
            await context.input("topic", "again")

        await flow.output("answer", "first")
        with pytest.raises(ValueError, match="already exists"):
            await context.save("answer", "second")

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="single-assignment",
        inputs={"topic": "override"},
    )

    assert result.status == "ok"
    assert (
        await anyio.Path(
            result.run_dir,
            "input",
            "topic.md",
        ).read_text()
        == "override"
    )
    assert (
        await anyio.Path(
            result.run_dir,
            "bindings",
            "answer.md",
        ).read_text()
        == "first"
    )


@pytest.mark.anyio
async def test_legacy_agent_is_an_async_callable_using_the_injected_runner(
    tmp_path,
) -> None:
    received: list[tuple[AgentConfig, AgentInvocation]] = []
    config = AgentConfig(
        name="legacy",
        system_prompt="Answer directly.",
        context_schema=("topic",),
    )
    legacy = Agent(config)
    assert getattr(legacy, "__agentName") == "legacy"
    assert getattr(legacy, "__config") == config
    assert vars(legacy)["agent_name"] == "legacy"
    assert vars(legacy)["config"] == config

    async def runner(
        runner_config: AgentConfig,
        invocation: AgentInvocation,
    ) -> SessionResult:
        received.append((runner_config, invocation))
        assert invocation.context is not None
        return SessionResult(
            text=f"answer:{invocation.context['topic']}",
            input_tokens=3,
            output_tokens=2,
        )

    async def program(_: RunContext) -> None:
        invocation = AgentInvocation(
            prompt="question",
            context={"topic": "Python"},
        )
        assert await legacy(invocation) == "answer:Python"
        assert await legacy(invocation) == "answer:Python"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="legacy-agent",
        runner=runner,
    )

    assert inspect.iscoroutinefunction(legacy)
    effective_config = AgentConfig(
        name="legacy",
        system_prompt="Answer directly.",
        max_tokens=8192,
        temperature=1.0,
        context_schema=("topic",),
    )
    assert received == [
        (
            effective_config,
            AgentInvocation(
                prompt="question",
                context={"topic": "Python"},
            ),
        ),
        (
            effective_config,
            AgentInvocation(
                prompt="question",
                context={"topic": "Python"},
            ),
        ),
    ]
    run_dir = anyio.Path(result.run_dir)
    first_trace = json.loads(await anyio.Path(run_dir, "trace", "legacy.json").read_text())
    second_trace = json.loads(await anyio.Path(run_dir, "trace", "legacy.2.json").read_text())
    assert first_trace["output_summary"] == "answer:Python"
    assert first_trace["tokens"] == {"calls": 1, "input": 3, "output": 2}
    assert first_trace["duration_ms"] is not None
    assert second_trace["output_summary"] == "answer:Python"
    assert not await anyio.Path(run_dir, "bindings", "legacy.md").exists()
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    assert graph["root"]["children"] == []
    assert not await anyio.Path(run_dir, "progress.jsonl").exists()
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["session_calls"] == {"legacy": 2}


@pytest.mark.anyio
async def test_legacy_agent_prefers_explicit_runner_inside_run(tmp_path) -> None:
    calls: list[str] = []
    config = AgentConfig(name="explicit", system_prompt="Answer directly.")

    async def explicit_runner(
        _: AgentConfig,
        __: AgentInvocation,
    ) -> str:
        calls.append("explicit")
        return "explicit answer"

    async def active_runner(
        _: AgentConfig,
        __: AgentInvocation,
    ) -> str:
        calls.append("active")
        return "active answer"

    agent = Agent(config, runner=explicit_runner)

    async def program(_: RunContext) -> None:
        assert await agent(AgentInvocation(prompt="question")) == "explicit answer"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="legacy-explicit-runner",
        runner=active_runner,
    )

    assert calls == ["explicit"]
    assert await anyio.Path(result.run_dir, "trace", "explicit.json").exists()


@pytest.mark.anyio
async def test_legacy_agent_shares_successful_ordinals_with_session(tmp_path) -> None:
    calls = 0
    config = AgentConfig(name="shared", system_prompt="Answer directly.")
    legacy = Agent(config)
    handle = flow.agent(config)

    async def runner(
        _: AgentConfig,
        invocation: AgentInvocation,
    ) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return invocation.prompt

    async def program(_: RunContext) -> None:
        with pytest.raises(RuntimeError, match="transient"):
            await legacy(AgentInvocation(prompt="failed"))
        assert await legacy(AgentInvocation(prompt="legacy")) == "legacy"
        assert await flow.session(handle, "session") == "session"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="legacy-shared-ordinal",
        runner=runner,
    )
    run_dir = anyio.Path(result.run_dir)

    assert await anyio.Path(run_dir, "trace", "shared.json").exists()
    assert await anyio.Path(run_dir, "trace", "shared.2.json").exists()
    assert not await anyio.Path(run_dir, "bindings", "shared.md").exists()
    assert await anyio.Path(run_dir, "bindings", "shared.2.md").read_text() == "session"
    graph = json.loads(await anyio.Path(run_dir, "execution-graph.json").read_text())
    assert [node["label"] for node in graph["root"]["children"]] == ["shared"]


@pytest.mark.anyio
async def test_legacy_agent_resume_preserves_traces_and_shared_ordinals(
    tmp_path,
) -> None:
    config = AgentConfig(name="shared", system_prompt="Answer directly.")
    legacy = Agent(config)
    handle = flow.agent(config)

    async def runner(_: AgentConfig, invocation: AgentInvocation) -> str:
        return invocation.prompt

    async def seed(_: RunContext) -> None:
        assert await legacy(AgentInvocation(prompt="seed")) == "seed"

    await run(
        seed,
        runs_dir=tmp_path,
        run_id="legacy-resume-ordinal",
        runner=runner,
        throw_on_error=True,
    )

    async def resumed(_: RunContext) -> None:
        assert await legacy(AgentInvocation(prompt="legacy-resumed")) == "legacy-resumed"
        assert await flow.session(handle, "session-resumed") == "session-resumed"
        assert (
            await flow.evaluate(
                question="Is this true?",
                kind="boolean",
                agent=handle,
            )
            is True
        )

    answers = iter(("legacy-resumed", "session-resumed", '{"value": true}'))

    async def resumed_runner(
        _: AgentConfig,
        __: AgentInvocation,
    ) -> str:
        return next(answers)

    result = await run(
        resumed,
        runs_dir=tmp_path,
        resume_from_run_id="legacy-resume-ordinal",
        runner=resumed_runner,
        throw_on_error=True,
    )
    run_dir = anyio.Path(result.run_dir)

    seed_trace = json.loads(
        await anyio.Path(run_dir, "trace", "shared.json").read_text(),
    )
    resumed_trace = json.loads(
        await anyio.Path(run_dir, "trace", "shared.2.json").read_text(),
    )
    assert seed_trace["output_summary"] == "seed"
    assert resumed_trace["output_summary"] == "legacy-resumed"
    assert await anyio.Path(run_dir, "bindings", "shared.3.md").read_text() == "session-resumed"
    assert await anyio.Path(
        run_dir,
        "bindings",
        "evaluate.shared.4.md",
    ).exists()
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["session_calls"] == {"shared": 3}


@pytest.mark.anyio
async def test_legacy_agent_cancellation_commits_trace_and_shared_ordinal(
    tmp_path,
) -> None:
    config = AgentConfig(name="shared", system_prompt="Answer directly.")
    legacy = Agent(config)
    handle = flow.agent(config)
    cancel_scope: anyio.CancelScope | None = None
    calls = 0

    async def runner(_: AgentConfig, invocation: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert cancel_scope is not None
            cancel_scope.cancel()
        return invocation.prompt

    async def program(context: RunContext) -> None:
        nonlocal cancel_scope
        lock_held = anyio.Event()

        async def hold_context_lock() -> None:
            async with context._lock:
                lock_held.set()
                await anyio.sleep_forever()

        with anyio.CancelScope() as scope:
            cancel_scope = scope
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(hold_context_lock)
                await lock_held.wait()
                await legacy(AgentInvocation(prompt="legacy"))

        assert await flow.session(handle, "session") == "session"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="legacy-cancel-ordinal",
        runner=runner,
        throw_on_error=True,
    )
    run_dir = anyio.Path(result.run_dir)

    legacy_trace = json.loads(
        await anyio.Path(run_dir, "trace", "shared.json").read_text(),
    )
    assert legacy_trace["output_summary"] == "legacy"
    assert await anyio.Path(run_dir, "bindings", "shared.2.md").read_text() == "session"
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["session_calls"] == {"shared": 2}


@pytest.mark.anyio
async def test_legacy_agent_commits_ordinal_when_trace_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    config = AgentConfig(name="shared", system_prompt="Answer directly.")
    legacy = Agent(config)
    handle = flow.agent(config)
    original_atomic_write_json = runtime_module._atomic_write_json

    async def fail_legacy_trace(path: anyio.Path, value: dict[str, object]) -> None:
        if path.parent.name == "trace" and path.name == "shared.json":
            raise OSError("trace unavailable")
        await original_atomic_write_json(path, value)

    monkeypatch.setattr(
        runtime_module,
        "_atomic_write_json",
        fail_legacy_trace,
    )

    async def runner(_: AgentConfig, invocation: AgentInvocation) -> str:
        return invocation.prompt

    async def program(_: RunContext) -> None:
        assert await legacy(AgentInvocation(prompt="legacy")) == "legacy"
        assert await flow.session(handle, "session") == "session"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="legacy-trace-failure",
        runner=runner,
        throw_on_error=True,
    )
    run_dir = anyio.Path(result.run_dir)

    assert not await anyio.Path(run_dir, "trace", "shared.json").exists()
    assert await anyio.Path(run_dir, "bindings", "shared.2.md").read_text() == "session"
    assert not await anyio.Path(run_dir, "bindings", "shared.md").exists()


@pytest.mark.anyio
async def test_legacy_agent_commits_ordinal_when_trace_inspection_fails(
    tmp_path,
    monkeypatch,
) -> None:
    config = AgentConfig(name="shared", system_prompt="Answer directly.")
    legacy = Agent(config)
    handle = flow.agent(config)
    original_exists = anyio.Path.exists
    inspection_failed = False

    async def fail_legacy_trace_inspection(path: anyio.Path) -> bool:
        nonlocal inspection_failed
        if not inspection_failed and path.parent.name == "trace" and path.name == "shared.json":
            inspection_failed = True
            raise OSError("trace directory unavailable")
        return await original_exists(path)

    monkeypatch.setattr(anyio.Path, "exists", fail_legacy_trace_inspection)

    async def runner(_: AgentConfig, invocation: AgentInvocation) -> str:
        return invocation.prompt

    async def program(_: RunContext) -> None:
        assert await legacy(AgentInvocation(prompt="legacy")) == "legacy"
        assert await flow.session(handle, "session") == "session"

    result = await run(
        program,
        runs_dir=tmp_path,
        run_id="legacy-trace-inspection-failure",
        runner=runner,
        throw_on_error=True,
    )
    run_dir = anyio.Path(result.run_dir)

    assert inspection_failed
    assert not await anyio.Path(run_dir, "trace", "shared.json").exists()
    assert await anyio.Path(run_dir, "bindings", "shared.2.md").read_text() == "session"
    meta = json.loads(await anyio.Path(run_dir, "meta.json").read_text())
    assert meta["session_calls"] == {"shared": 2}


@pytest.mark.anyio
async def test_standalone_agent_with_explicit_runner_is_callable_outside_run() -> None:
    received: list[tuple[AgentConfig, AgentInvocation]] = []
    config = AgentConfig(name="standalone", system_prompt="Answer directly.")

    async def runner(
        runner_config: AgentConfig,
        invocation: AgentInvocation,
    ) -> SessionResult:
        received.append((runner_config, invocation))
        return SessionResult(text="standalone answer", input_tokens=2, output_tokens=1)

    agent = Agent(config, runner=runner)
    invocation = AgentInvocation(prompt="question")

    assert await agent(invocation) == "standalone answer"
    assert received == [
        (
            AgentConfig(
                name="standalone",
                system_prompt="Answer directly.",
                max_tokens=8192,
                temperature=1.0,
            ),
            invocation,
        )
    ]
