from __future__ import annotations

import inspect
import json
from typing import cast

import anyio
import pytest

from psi_agent.fusion_flow import (
    Agent,
    AgentConfig,
    AgentInvocation,
    ServiceParam,
    SessionResult,
    flow,
    run,
)
from psi_agent.fusion_flow.runtime import RunContext


def test_agent_returns_a_handle_without_an_active_run() -> None:
    config = AgentConfig(name="writer", system="Write clearly.")

    handle = flow.agent(config)

    assert handle.name == "writer"
    assert handle.config == config
    assert handle.kind == "agent"


@pytest.mark.anyio
async def test_session_requires_runner_only_when_called(tmp_path) -> None:
    handle = flow.agent(AgentConfig(name="writer", system="Write clearly."))

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
            system="Write clearly.",
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

    handle = flow.agent(AgentConfig(name="writer", system="Write clearly."))

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
    plain_trace = json.loads(await anyio.Path(run_dir, "trace", "writer.json").read_text())
    rich_trace = json.loads(await anyio.Path(run_dir, "trace", "writer.2.json").read_text())
    assert plain_trace["tokens"] == sessions[1]["tokens"]
    assert rich_trace["tokens"] == sessions[2]["tokens"]


@pytest.mark.anyio
async def test_session_failure_releases_explicit_binding(tmp_path) -> None:
    calls = 0

    async def runner(_: AgentConfig, __: AgentInvocation) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return "saved"

    handle = flow.agent(AgentConfig(name="writer", system="Write clearly."))

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

    handle = flow.agent(AgentConfig(name="writer", system="Write clearly."))

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
        system="Answer directly.",
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
    assert received == [
        (
            config,
            AgentInvocation(
                prompt="question",
                context={"topic": "Python"},
            ),
        ),
        (
            config,
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


@pytest.mark.anyio
async def test_legacy_agent_prefers_explicit_runner_inside_run(tmp_path) -> None:
    calls: list[str] = []
    config = AgentConfig(name="explicit", system="Answer directly.")

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
    config = AgentConfig(name="shared", system="Answer directly.")
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
async def test_standalone_agent_with_explicit_runner_is_callable_outside_run() -> None:
    received: list[tuple[AgentConfig, AgentInvocation]] = []
    config = AgentConfig(name="standalone", system="Answer directly.")

    async def runner(
        runner_config: AgentConfig,
        invocation: AgentInvocation,
    ) -> SessionResult:
        received.append((runner_config, invocation))
        return SessionResult(text="standalone answer", input_tokens=2, output_tokens=1)

    agent = Agent(config, runner=runner)
    invocation = AgentInvocation(prompt="question")

    assert await agent(invocation) == "standalone answer"
    assert received == [(config, invocation)]
